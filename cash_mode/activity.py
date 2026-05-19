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

EVENT_ALL_IN = "all_in"
"""An AI went all-in during a sim hand at an unseated table. Emitted
by the full-sim path (Commit 4) when `HandSimResult.hand_events`
contains an `all_in` HandEvent. The ALL_IN flag in the underlying
game state persists through pot award until reset, so this event
fires regardless of whether the all-in player won or lost — the
drama is the shove itself."""

EVENT_BUST = "bust"
"""An AI ended a sim hand with 0 chips. Distinct from `leave` (which
fires later, when movement decisions remove the bust seat from the
table on the next refresh tick): `bust` is the hand-level "they're
out of chips" beat; `leave` is the "they walked away" beat. Both
fire over the course of a single lobby read after a bust hand."""

EVENT_BURST_SUMMARY = "burst_summary"
"""Compression event for catch-up bursts (Commit 5): when a single
lobby read fires many sim hands at one table (e.g. after the
player closed the tab for 30 minutes), we emit at most one
big_win + one bust + one all_in per table per refresh, plus this
summary event noting "N more hands at $X — Napoleon +$220 net."
Keeps the ticker readable without losing the aggregate signal."""


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


def format_all_in_message(
    name: str, stake_label: str, opponent: Optional[str] = None,
) -> str:
    """Phrasing for an all-in event at an unseated table.

    `opponent` is shown when the all-in was heads-up vs an obvious
    counterparty; omitted in multiway pots where naming one
    opponent would be misleading."""
    if opponent:
        return f"{name} shoved all-in against {opponent} at {stake_label}"
    return f"{name} shoved all-in at {stake_label}"


def format_bust_message(name: str, stake_label: str) -> str:
    """Phrasing for a bust event — AI's stack hit 0 during a hand."""
    return f"{name} busted out at {stake_label}"


def format_burst_summary_message(
    stake_label: str, hands: int, top_name: Optional[str] = None,
    top_net_delta: int = 0,
) -> str:
    """Phrasing for the catch-up burst summary event (Commit 5).

    Reads like "...and 24 more hands at $50 — Napoleon +$1,200 net"
    when a leader is identifiable, falls back to a chip-neutral
    framing when net deltas are small."""
    base = f"...and {hands} more hands at {stake_label}"
    if top_name and abs(top_net_delta) >= 100:
        sign = "+" if top_net_delta >= 0 else "-"
        return f"{base} — {top_name} {sign}${abs(top_net_delta):,} net"
    return base


def serialize_event(event: LobbyEvent) -> dict:
    """JSON-friendly dict for the lobby response. Equivalent to
    dataclasses.asdict but explicit so changes here are visible at
    the wire boundary."""
    return asdict(event)
