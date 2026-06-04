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
from dataclasses import asdict, dataclass
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

EVENT_AI_BANKRUPTCY = "ai_bankruptcy"
"""An insolvent AI declared bankruptcy: their liquid bankroll was
liquidated and split pro-rata across every creditor, the remainder
of each carry written off as a default, and chips zeroed. The
terminal valve for a borrower who's underwater past the deadline and
can neither pay nor be forced through the narrower explicit-default
gate. Always surfaced on the ticker (bypasses the carry threshold) —
it's a lifecycle rupture, not a routine payoff."""

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

EVENT_WHALE_ARRIVAL = "whale_arrival"
"""A rare pool-funded high roller (a whale) just sat down at a real
cardroom (lobby) table — the top gate of the bank-pool dam. Mechanically
a whale is just a fish (archetype='fish') with a much deeper prefund, so
this is the flavor *and* the pull signal: grinders should come farm it.
`table_id`/`stake_label` point at the cardroom seat. See
`cash_mode/casino_provisioning.py:resolve_whale_provisioning` and
`docs/plans/CASH_MODE_WHALE_AT_CARDROOM.md`."""

EVENT_WHALE_DEPARTURE = "whale_departure"
"""A whale left the cardroom. Fires on the dam wind-down (pool drained
below the stake's floor → the relief valve recalls the whale's unused
stake to the pool). A whale that leaves via its own movement (busted,
or stormed off on tilt) surfaces as the usual EVENT_BUST / EVENT_LEAVE
instead — this event is only the provisioning-driven recall."""


EVENT_REPUTATION_SHIFT = "reputation_shift"
"""The human player's reputation quadrant changed (v121). Emitted by the
world ticker's prestige recompute when the cached quadrant flips (e.g.
Up-and-comer → Beloved Legend, or a slide into Infamous Villain). The beat
is keyed to the human, not an AI: `personality_id`/`name`/`table_id`/
`stake_label` are empty and `reason` carries the new quadrant label. This is
the read-only scoreboard's "the room sees you differently now" surface — it
does not change any AI behaviour. See
`docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`."""

EVENT_TOURNAMENT_MILESTONE = "tournament_milestone"
"""A circuit Main Event crossed a field-collapse milestone (P3.7). Emitted by
the world ticker as an *autonomous* (declined / expired, AI-only) Main Event
plays out at world pace. `reason` carries the milestone kind
(`final_table` | `heads_up` | `down_to`); `name`/`personality_id` are empty —
it's a field-wide beat, not one persona. Structural-only by design: per-hand
knockouts and table breaks are NOT surfaced (the "never every hand" filter). See
`docs/plans/P3_REMAINING_HANDOFF.md` §P3.7."""

EVENT_TOURNAMENT_BUBBLE = "tournament_bubble"
"""The Main Event bubble burst (P3.7) — the last finisher before the money is
out, so everyone left is paid. Companion to EVENT_TOURNAMENT_MILESTONE."""

EVENT_TOURNAMENT_WINNER = "tournament_winner"
"""A circuit Main Event was won (P3.7). `name` is the champion; emitted once when
the autonomous field collapses to a winner on the settling tick."""


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
    # Groups every event emitted from one sim hand (single-hand path only).
    # None for non-hand events (join/leave/stake/vice) and burst-compressed
    # events, which don't belong to one resolvable hand.
    hand_id: Optional[str] = None
    # Whether the lobby ticker should render this row. The single-hand path
    # emits one composed `primary=True` summary per hand ("X shoved all-in
    # and won $Y, busting Z") and demotes the atomic big_win/big_loss/
    # all_in/bust events from that hand to `primary=False` — kept in the
    # buffer for per-AI filtering, hidden from the ticker so a hand reads as
    # one coherent line instead of a mis-ordered cluster.
    primary: bool = True


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
    limit: int = 10,
    *,
    sandbox_id: Optional[str] = None,
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
        snapshot = [e for e in snapshot if e.sandbox_id is None or e.sandbox_id == sandbox_id]
    return snapshot[:limit]


def clear_events() -> None:
    """Drop all buffered events. For tests."""
    with _events_lock:
        _events.clear()


# --- Message formatters -----------------------------------------------------


def format_table_location(table_name: Optional[str], stake_label: str) -> str:
    """Where-label for table-specific feed events: the familiar table name
    with its stake in brackets ("The Lodge [$50]") so players recognize the
    spot from the lobby and know where to find the action. Falls back to
    "the $50 table" for unnamed (private/legacy) rows. Reads naturally after
    "at"/"on"/"from", so formatters drop it straight in where they used to
    interpolate the bare stake.

    Only table-specific events use this. Tier-scoped events (AI-to-AI
    stakes / carries) keep the bare stake label, since they reference the
    stake tier rather than one particular table.
    """
    if table_name:
        return f"{table_name} [{stake_label}]"
    return f"the {stake_label} table"


# Reason → user-facing phrasing for leave events. Frozen here so the
# lobby route stays a thin serializer.
_LEAVE_PHRASES = {
    "forced_leave": "busted out at",
    "stake_up_queued": "won big and is shopping up from",
    "take_break": "is taking a break from",
    "bored_move": "moved on from",
}


def format_leave_message(
    name: str, stake_label: str, reason: str, table_name: Optional[str] = None
) -> str:
    """Human-readable phrasing for a leave event."""
    verb = _LEAVE_PHRASES.get(reason, "left")
    return f"{name} {verb} {format_table_location(table_name, stake_label)}"


def format_join_message(name: str, stake_label: str, table_name: Optional[str] = None) -> str:
    """Human-readable phrasing for a join event."""
    return f"{name} sat down at {format_table_location(table_name, stake_label)}"


def format_big_win_message(
    winner: str,
    loser: str,
    stake_label: str,
    amount: int,
    table_name: Optional[str] = None,
) -> str:
    """Phrasing for a fake-sim big-win event."""
    where = format_table_location(table_name, stake_label)
    return f"{winner} won ${amount:,} off {loser} at {where}"


def format_big_loss_message(
    loser: str,
    winner: str,
    stake_label: str,
    amount: int,
    table_name: Optional[str] = None,
) -> str:
    """Phrasing for a fake-sim big-loss event (the loser's POV).

    Symmetric phrasing to the win event; emitted alongside so the
    ticker can show whichever framing reads best. The lobby keeps
    only one of the pair (win is the more dramatic verb), but both
    are recorded so future filtering / per-personality feeds can
    pick either side."""
    where = format_table_location(table_name, stake_label)
    return f"{loser} dropped ${amount:,} to {winner} at {where}"


def format_all_in_message(
    name: str,
    stake_label: str,
    opponent: Optional[str] = None,
    table_name: Optional[str] = None,
) -> str:
    """Phrasing for an all-in event at an unseated table.

    `opponent` is shown when the all-in was heads-up vs an obvious
    counterparty; omitted in multiway pots where naming one
    opponent would be misleading."""
    where = format_table_location(table_name, stake_label)
    if opponent:
        return f"{name} shoved all-in against {opponent} at {where}"
    return f"{name} shoved all-in at {where}"


def format_bust_message(name: str, stake_label: str, table_name: Optional[str] = None) -> str:
    """Phrasing for a bust event — AI's stack hit 0 during a hand."""
    return f"{name} busted out at {format_table_location(table_name, stake_label)}"


def _join_names(names: List[str]) -> str:
    """Oxford-ish join, collapsing a long tail to "and N more"."""
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]}, {names[1]}, and {len(names) - 2} more"


def format_hand_summary_message(
    *,
    winner: Optional[str],
    loser: Optional[str],
    amount: int,
    stake_label: str,
    winner_shoved: bool,
    busted_names: List[str],
    table_name: Optional[str] = None,
) -> str:
    """Compose ONE sentence for a single sim hand.

    Folds a hand's beats into a single primary ticker line so the feed
    reads as one coherent event instead of a mis-ordered cluster of
    win/all-in/bust rows. `winner`/`loser` are display names (winner is
    None for a bust-only hand that didn't cross the big-pot threshold);
    `busted_names` is everyone who hit 0 this hand.

    Note: in a multiway hand the bust clause is attributed to the headline
    winner, which can slightly over-attribute a side-pot bust. The sims are
    near-heads-up in practice (opponents are named ~96% of the time), so
    this stays accurate for the common case and readable for the rare one.
    """
    where = format_table_location(table_name, stake_label)
    # Bust-only hand: no headline win above threshold.
    if not winner or amount <= 0:
        who = _join_names(busted_names)
        if not who:
            return ""
        tail = f" to {winner}" if winner and len(busted_names) == 1 else ""
        return f"{who} busted out{tail} at {where}"

    lead = (
        f"{winner} shoved all-in and won ${amount:,}"
        if winner_shoved
        else f"{winner} won ${amount:,}"
    )
    if busted_names:
        return f"{lead}, busting {_join_names(busted_names)} at {where}"
    if loser:
        return f"{lead} off {loser} at {where}"
    return f"{lead} at {where}"


def format_last_stand_message(name: str, stake_label: str, table_name: Optional[str] = None) -> str:
    """Phrasing for an AI's last-stand event — their whole bankroll is
    now on the table. Framed so the player reads it as an opening: a
    seat worth targeting because the occupant has nothing left to fall
    back on."""
    return f"{name} has their whole bankroll on {format_table_location(table_name, stake_label)}"


def format_player_last_stand_message(stake_label: str, table_name: Optional[str] = None) -> str:
    """Phrasing for the player's own last-stand line — a self-warning
    that they're playing without a reserve. Second-person so it reads
    as a heads-up, not a spectator beat."""
    return f"Your whole bankroll is on {format_table_location(table_name, stake_label)}"


def format_ai_stake_message(
    staker_name: str,
    borrower_name: str,
    stake_label: str,
    principal: int,
) -> str:
    """Phrasing for an AI-to-AI stake creation."""
    return f"{staker_name} staked {borrower_name} for ${principal:,} at {stake_label}"


def format_ai_default_message(
    borrower_name: str,
    staker_name: str,
    stake_label: str,
    carry_amount: int,
) -> str:
    """Phrasing for an AI-to-AI stake carry — borrower busted owing."""
    return f"{borrower_name} carried ${carry_amount:,} from {staker_name} at {stake_label}"


def format_ai_explicit_default_message(
    borrower_name: str,
    staker_name: str,
    stake_label: str,
    carry_amount: int,
) -> str:
    """Phrasing for an AI explicitly walking away from a carry.

    Phase 4.5 Commit 5. Distinct verb from the natural-carry message
    so the ticker reads as a deliberate reputation-burning act, not
    just "they busted owing." The relationship-axis hit is meaningfully
    sharper (STAKE_DEFAULTED vs no-op for natural carry) and the
    in-game story benefits from the harder framing."""
    return f"{borrower_name} burned ${carry_amount:,} owed to {staker_name} at {stake_label}"


def format_ai_payoff_message(
    borrower_name: str,
    staker_name: str,
    stake_label: str,
    amount: int,
) -> str:
    """Phrasing for an AI voluntarily clearing a carry.

    Phase 4.5 Commit 3. Reads as the AI doing the right thing —
    bankroll → staker, status flips to settled, STAKE_REPAID fires."""
    return f"{borrower_name} paid off ${amount:,} carry to {staker_name} at {stake_label}"


def format_ai_bankruptcy_message(
    borrower_name: str,
    recovered: int,
    total_debt: int,
) -> str:
    """Phrasing for an AI declaring bankruptcy.

    The bankruptcy valve liquidates the AI's chips and splits them
    pro-rata across all creditors; the rest is discharged as default.
    `recovered` is what creditors got back in aggregate, `total_debt`
    is what they were owed — the gap is the collective write-off. One
    aggregate beat rather than per-creditor spam."""
    return (
        f"{borrower_name} declared bankruptcy — creditors recovered "
        f"${recovered:,} of ${total_debt:,}"
    )


def format_ai_forgiven_message(
    staker_name: str,
    borrower_name: str,
    stake_label: str,
    amount: int,
) -> str:
    """Phrasing for an AI staker forgiving an AI borrower's carry.

    Phase 4.5 Commit 4. The staker is the actor (they chose to
    forgive), so they lead the phrasing."""
    return f"{staker_name} forgave {borrower_name}'s ${amount:,} carry at {stake_label}"


def format_ai_requests_forgiveness_message(
    borrower_name: str,
    stake_label: str,
    amount: int,
) -> str:
    """Phrasing for an AI asking the human staker for forgiveness.

    v110 — the player decides (grant/refuse via /staker-forgive).
    Phrased as a direct ask so the player notices the request needs
    their attention; the wallet badge + Forgiveness Requests section
    in the Net Worth Drawer carry the actual decision UI."""
    return f"{borrower_name} is asking you to forgive their ${amount:,} {stake_label} carry"


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
    stake_label: str,
    hands: int,
    top_name: Optional[str] = None,
    top_net_delta: int = 0,
    table_name: Optional[str] = None,
) -> str:
    """Phrasing for the catch-up burst summary event (Commit 5).

    Reads like "...and 24 more hands at The Lodge [$50] — Napoleon +$1,200
    net" when a leader is identifiable, falls back to a chip-neutral
    framing when net deltas are small."""
    base = f"...and {hands} more hands at {format_table_location(table_name, stake_label)}"
    if top_name and abs(top_net_delta) >= 100:
        sign = "+" if top_net_delta >= 0 else "-"
        return f"{base} — {top_name} {sign}${abs(top_net_delta):,} net"
    return base


# Reputation-quadrant → ticker phrasing for the human's prestige shift
# (v121). Second-person — it's a beat about *you*, not a spectator line.
_REPUTATION_SHIFT_PHRASES = {
    "Beloved Legend": "The room has come to adore you — you're a Beloved Legend now",
    "Infamous Villain": "Word's gotten around — you're an Infamous Villain now",
    "Up-and-comer": "People are starting to notice you — you're an Up-and-comer",
    "Disliked Nobody": "The room has soured on you",
}


def format_reputation_shift_message(quadrant: str) -> str:
    """Human-readable phrasing for a player reputation-quadrant change."""
    return _REPUTATION_SHIFT_PHRASES.get(quadrant, f"The room now sees you as a {quadrant}")


def format_tournament_milestone_message(kind: str, remaining: int) -> str:
    """Ticker phrasing for a Main Event field-collapse milestone (P3.7)."""
    if kind == 'final_table':
        return f"Main Event: down to the final table ({remaining} left)"
    if kind == 'heads_up':
        return "Main Event: heads-up for the title"
    return f"Main Event: field down to {remaining}"


def format_tournament_bubble_message(paid_places: int) -> str:
    """Ticker phrasing for the Main Event bubble bursting (P3.7)."""
    return f"Main Event: the bubble burst — {paid_places} in the money"


def format_tournament_winner_message(name: str) -> str:
    """Ticker phrasing for a Main Event champion (P3.7)."""
    return f"Main Event: {name or 'a challenger'} wins the title"


def serialize_event(event: LobbyEvent) -> dict:
    """JSON-friendly dict for the lobby response.

    `sandbox_id` is server-internal scoping (see `LobbyEvent` docs) —
    stripped from the wire payload so the frontend's event type
    surface stays unchanged across the Phase 4 prep refactor.
    """
    payload = asdict(event)
    payload.pop('sandbox_id', None)
    return payload
