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

EVENT_LAST_STAND = "last_stand"
"""An AI (or the player) has their entire net worth on a single table —
reserve bankroll is $0, so the stack on the felt is literally all they
have left. This is the only state in which busting their table stack
*completely* crashes them out (any reserve at all and they'd just go idle
+ side-hustle back), which is exactly why it's the *predator* signal: it
tells the player whose elimination is actually on the table right now so
they can sit down and finish them. Distinct from `bust` (already out of
chips this hand) and from the side hustle (an *idle* broke AI that left
the felt to earn) — `last_stand` is the still-seated "last chips" beat.

Emitted once per episode: re-entering the committed state after recovering
(or after leaving and coming back) re-triggers it, but a steady committed
seat doesn't re-flood the ticker every refresh. `reason` is `''` for AIs
and `'self'` for the player's own line."""

EVENT_AI_STAKE = "ai_stake"
"""An AI staked another AI. Phase 4 of the backing system. Surfaces
the AI economy as visible drama in the lobby ticker — "Bezos staked
Napoleon for $2,000 at $50". Throttled by chip-amount threshold so
the smallest stakes (at $2 / $10 tables) don't drown the ticker."""

EVENT_AI_DEFAULT = "ai_default"
"""An AI-to-AI stake settled with a carry — borrower busted without
repaying principal. Phase 4. The "default" framing is the player's
perspective; technically the stake row's status flips to 'carry',
not 'defaulted' (Phase 4 natural-carry case) OR 'defaulted' (Phase
4.5 Commit 5 explicit-default case). The two are distinguishable
on the wire via the `message` verb ("carried" vs "burned"). Surfaces
alongside EVENT_AI_STAKE to make the AI economy's wins and losses
both visible."""

EVENT_AI_PAYOFF = "ai_payoff"
"""An AI cleared an outstanding carry to another AI by paying off
from bankroll. Phase 4.5 Commit 3 — the "AI hits the gym, returns
ready to clear his tab" beat. Threshold-gated the same as
EVENT_AI_STAKE/EVENT_AI_DEFAULT so small-stake payoffs (at $2 / $10
tables) stay invisible."""

EVENT_AI_FORGIVEN = "ai_forgiven"
"""An AI forgave another AI's outstanding carry on request. Phase
4.5 Commit 4 — the "generous staker writes off the debt" beat.
Threshold-gated. The refused-forgiveness path is intentionally
silent on the ticker (the relationship axis hit is enough; not
every refusal needs to surface)."""

EVENT_AI_REQUESTS_FORGIVENESS = "ai_requests_forgiveness"
"""An AI is asking the human staker to forgive a carry. v110 —
when the AI auto-forgive path would have fired against a human-
staker carry, instead we stamp `pending_forgiveness_ask` and emit
this ticker beat so the player notices the ask landed. Resolution
(grant/refuse) happens via POST /api/cash/stakes/<id>/staker-forgive
and surfaces as STAKE_FORGIVEN / STAKE_FORGIVENESS_REFUSED in the
relationship axes — no separate ticker line for the resolution,
since the badge / Net Worth Drawer surface carries it."""

EVENT_BURST_SUMMARY = "burst_summary"
"""Compression event for catch-up bursts (Commit 5): when a single
lobby read fires many sim hands at one table (e.g. after the
player closed the tab for 30 minutes), we emit at most one
big_win + one bust + one all_in per table per refresh, plus this
summary event noting "N more hands at $X — Napoleon +$220 net."
Keeps the ticker readable without losing the aggregate signal."""

EVENT_VICE_START = "vice_start"
"""An AI fired a vice and went off-grid. Carries the narration
("Napoleon commissioned an oversized bronze bust...") as `message`
and the duration bucket as `reason`. `stake_label` is empty — vice
is a between-tables activity. Frontend renders a dimmed "Away"
state for the personality card."""

EVENT_VICE_END = "vice_end"
"""An AI's vice expired; they're back in the eligibility pool. The
psych-recovery side effect has already run. `message` is a short
return phrase ("{name} is back"); the original narration isn't
re-rendered (the player saw it on the start event). `reason` is
the duration_bucket that just finished."""

EVENT_HUSTLE_START = "hustle_start"
"""A broke AI went off-grid to a side hustle (the mirror of vice).
Carries the narration ("Napoleon is flipping a small business...") as
`message` and the duration bucket as `reason`. `stake_label` is empty —
the hustle is a between-tables activity. Frontend renders the same
dimmed "Away" state as vice. See `docs/plans/CASH_MODE_SIDE_HUSTLE.md`."""

EVENT_HUSTLE_END = "hustle_end"
"""An AI's side hustle expired; they're back with a pool-funded lump
(or empty-handed if the pool was dry). `message` is a short return
phrase ("{name} is back with $X" / "{name} is back"); `reason` is the
duration_bucket that just finished. Mirror of EVENT_VICE_END."""


@dataclass(frozen=True)
class LobbyEvent:
    """One movement event surfaced to the lobby UI.

    Fields are flat strings/ints so the JSON shape is obvious to the
    frontend. `message` is the pre-formatted display string ("Napoleon
    busted out of $50") so the React side doesn't have to know about
    every enum value; the structured fields are still there for any
    future grouping/filtering UI.

    `sandbox_id` is server-internal scoping — set by every emitter
    (lobby.py + Phase 4 stake events). `recent_events(sandbox_id=...)`
    filters on it so events from another player's sandbox don't leak
    into this player's ticker. Stripped from `serialize_event` so the
    wire shape is unchanged. None tolerated for legacy events still
    in the ring buffer at process startup — those degrade to "visible
    everywhere" rather than "lost," matching the buffer's best-effort
    semantics. Phase 4 prep.
    """

    type: str
    table_id: str
    stake_label: str
    personality_id: str
    name: str
    reason: str  # `''` for joins; movement decision for leaves
    message: str
    created_at: str  # ISO-8601, UTC
    sandbox_id: Optional[str] = None


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


def recent_events(
    limit: int = 10, *, sandbox_id: Optional[str] = None,
) -> List[LobbyEvent]:
    """Return up to `limit` most-recent events, newest first.

    Snapshot — callers don't hold the lock; mutations after this
    call don't reflect in the returned list. Safe to serialize.

    `sandbox_id` filters the ring to events scoped to this sandbox
    (plus pre-scoping events with `sandbox_id=None`, which match
    everywhere as a best-effort upgrade path). When omitted (None
    keyword default), no filter is applied — admin / cross-sandbox
    callers see the full ring.
    """
    with _events_lock:
        snapshot = list(_events)
    snapshot.reverse()
    if sandbox_id is not None:
        snapshot = [
            e for e in snapshot
            if e.sandbox_id is None or e.sandbox_id == sandbox_id
        ]
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


def format_last_stand_message(name: str, stake_label: str) -> str:
    """Phrasing for an AI's last-stand event — their whole bankroll is
    now on the table. Framed so the player reads it as an opening: a
    seat worth targeting because the occupant has nothing left to fall
    back on."""
    return f"{name} has their whole bankroll on the {stake_label} table"


def format_player_last_stand_message(stake_label: str) -> str:
    """Phrasing for the player's own last-stand line — a self-warning
    that they're playing without a reserve. Second-person so it reads
    as a heads-up, not a spectator beat."""
    return f"Your whole bankroll is on the {stake_label} table"


def format_ai_stake_message(
    staker_name: str, borrower_name: str, stake_label: str, principal: int,
) -> str:
    """Phrasing for an AI-to-AI stake creation."""
    return (
        f"{staker_name} staked {borrower_name} for ${principal:,} at {stake_label}"
    )


def format_ai_default_message(
    borrower_name: str, staker_name: str, stake_label: str, carry_amount: int,
) -> str:
    """Phrasing for an AI-to-AI stake carry — borrower busted owing."""
    return (
        f"{borrower_name} carried ${carry_amount:,} from {staker_name} at {stake_label}"
    )


def format_ai_explicit_default_message(
    borrower_name: str, staker_name: str, stake_label: str, carry_amount: int,
) -> str:
    """Phrasing for an AI explicitly walking away from a carry.

    Phase 4.5 Commit 5. Distinct verb from the natural-carry message
    so the ticker reads as a deliberate reputation-burning act, not
    just "they busted owing." The relationship-axis hit is meaningfully
    sharper (STAKE_DEFAULTED vs no-op for natural carry) and the
    in-game story benefits from the harder framing."""
    return (
        f"{borrower_name} burned ${carry_amount:,} owed to {staker_name} at {stake_label}"
    )


def format_ai_payoff_message(
    borrower_name: str, staker_name: str, stake_label: str, amount: int,
) -> str:
    """Phrasing for an AI voluntarily clearing a carry.

    Phase 4.5 Commit 3. Reads as the AI doing the right thing —
    bankroll → staker, status flips to settled, STAKE_REPAID fires."""
    return (
        f"{borrower_name} paid off ${amount:,} carry to {staker_name} at {stake_label}"
    )


def format_ai_forgiven_message(
    staker_name: str, borrower_name: str, stake_label: str, amount: int,
) -> str:
    """Phrasing for an AI staker forgiving an AI borrower's carry.

    Phase 4.5 Commit 4. The staker is the actor (they chose to
    forgive), so they lead the phrasing."""
    return (
        f"{staker_name} forgave {borrower_name}'s ${amount:,} carry at {stake_label}"
    )


def format_ai_requests_forgiveness_message(
    borrower_name: str, stake_label: str, amount: int,
) -> str:
    """Phrasing for an AI asking the human staker for forgiveness.

    v110 — the player decides (grant/refuse via /staker-forgive).
    Phrased as a direct ask so the player notices the request needs
    their attention; the wallet badge + Forgiveness Requests section
    in the Net Worth Drawer carry the actual decision UI."""
    return (
        f"{borrower_name} is asking you to forgive their ${amount:,} {stake_label} carry"
    )


# Phase 4.5 ticker-throttle threshold for carry-resolution events
# (EVENT_AI_PAYOFF / EVENT_AI_FORGIVEN / explicit EVENT_AI_DEFAULT).
# Mirrors AI_STAKE_TICKER_THRESHOLD so the four Phase-4/4.5 AI-economy
# events share one drama floor.
AI_CARRY_TICKER_THRESHOLD = 2000


# Phase 4 ticker-throttle threshold. AI stakes below this principal
# fire silently — relationship + chip state mutate, but no event
# surfaces. Tuned so $2 and $10 stakes stay invisible (their min
# buy-ins are 80 and 400, both well below 2000) and $50+ stakes
# show up. Matches the spec's "drama threshold" guidance.
AI_STAKE_TICKER_THRESHOLD = 2000


def format_vice_start_message(name: str, narration: str) -> str:
    """The narration leads the start message; we ensure the name is in it.

    The LLM is prompted to lead with the character's name ("Napoleon
    commissioned..."), but models occasionally drop it ("Pre-ordered a
    private jet..."). That leaves the ticker reading like an
    unattributed quote, which is what the user reported. As a defensive
    fallback, prepend `{name} — ` when the narration doesn't already
    lead with the personality name. Case-insensitive match so
    "napoleon" or "Napoleon's" both count as name-led.
    """
    narration = narration.strip()
    if not narration:
        # Degenerate fallback — at least say WHO went off-grid.
        return f"{name} stepped out"
    if narration.lower().startswith(name.lower()):
        return narration
    return f"{name} — {narration}"


def format_vice_end_message(name: str) -> str:
    """Short return phrase. The full narration already showed at start.

    Kept deliberately terse so a player who's been away doesn't read
    a wall of vice-end events after a long session. The drama was at
    the start; the end is just a status flip.
    """
    return f"{name} is back"


def format_hustle_start_message(name: str, narration: str) -> str:
    """The narration leads the start message; ensure the name is in it.

    Identical defensive shape to `format_vice_start_message` — the LLM is
    asked to lead with the character's name, but models occasionally drop
    it, leaving the ticker reading as an unattributed quote. Prepend
    `{name} — ` when the narration doesn't already lead with the name.
    """
    narration = narration.strip()
    if not narration:
        return f"{name} stepped out to earn"
    if narration.lower().startswith(name.lower()):
        return narration
    return f"{name} — {narration}"


def format_hustle_end_message(name: str, paid_amount: int = 0) -> str:
    """Short return phrase. The full narration already showed at start.

    Surfaces the payout when the pool funded one ("{name} is back with
    $X"); falls back to the terse vice-style phrasing when the hustle
    returned empty-handed (pool was dry), since "back with $0" reads as a
    bug rather than a beat.
    """
    if paid_amount > 0:
        return f"{name} is back with ${paid_amount:,}"
    return f"{name} is back"


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
    """JSON-friendly dict for the lobby response.

    `sandbox_id` is server-internal scoping (see `LobbyEvent` docs) —
    stripped from the wire payload so the frontend's event type
    surface stays unchanged across the Phase 4 prep refactor.
    """
    payload = asdict(event)
    payload.pop('sandbox_id', None)
    return payload
