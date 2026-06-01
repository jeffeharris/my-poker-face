"""Entity-Presence state machine — pure, non-authoritative (Cut 3, Phase 2 core).

This module implements the *Presence* half of the two-machine model described in
``docs/plans/CASH_MODE_STATE_MODEL.md`` (§5.1, §6). It answers one question:
**"where is this actor?"** — and makes the ``seated_and_idle`` / ``double_seat``
contradiction class *unrepresentable* rather than merely detected.

Status: ADDITIVE AND DORMANT. Nothing in the live cash-mode codepaths calls this
yet. The pure machine here and its backing table (``entity_presence``) exist so a
later, human-reviewed phase can reroute the seat (``save_table``, 25 callsites) /
idle-pool / hustle / vice writers through it (see
``docs/plans/CASH_MODE_PRESENCE_MIGRATION.md``).

Design contract (mirrors ``poker/poker_state_machine.py``):

- **Frozen dataclasses, pure transitions.** ``transition(state, event) -> state``
  returns a *new* ``PresenceState`` and never mutates its input. No I/O, no locks,
  no clock reads (timestamps are supplied by the caller / persistence layer).
- **One state per ``(entity_id, sandbox_id)``.** A single state value — not a set
  of flags — is what forbids ``seated_and_idle``: an entity the machine reports as
  ``SEATED`` literally cannot also be ``IDLE``. There is no representation for two
  states at once.
- **Illegal transitions raise** ``IllegalPresenceTransition``. The legal edge set
  (``LEGAL_TRANSITIONS``) is the spec; anything outside it is rejected. This is
  what makes the bug classes structural.
- **Atomicity is the caller's job.** Per the design §6.1, these functions are pure;
  the caller must hold ``get_sandbox_lock(sandbox_id)`` across a read → transition
  → persist cycle so presence + chip-custody + session commit together. The
  machine *enforces legal state*; it does **not** guarantee atomicity.

Entity id convention (shared with the chip ledger, see
``core/economy/ledger.py`` — note: distinct from ``flask_app/services/presence.py``,
which is the unrelated Socket.IO *connection*-presence tracker; this module is the
cash-mode *seat/idle* presence state machine):

- ``player:<owner_id>`` — a human player.
- ``ai:<personality_id>`` — an AI player with a real (pool- or bankroll-funded)
  identity.

States (§5.1 + §6.2 ``POOL`` origin):

- ``OFFLINE``      — not present in this sandbox (human cashed out / AI not seeded).
- ``SEATED``       — at a table; carries ``table_id`` + ``seat_index``.
- ``IDLE``         — present but between tables (the idle pool — a projection).
- ``SIDE_HUSTLE``  — AI off-grid earning (a projection of ``ai_side_hustle_state``).
- ``VICE``         — AI off-grid spending (a projection of ``ai_vice_state``).
- ``POOL``         — origin state for pool-funded casino AI that has no
                     ``OFFLINE`` / bankroll analogue (§6.2). Casino provisioning
                     seats *from* ``POOL``; on leaving such an AI returns to
                     ``POOL`` rather than ``OFFLINE``.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class PresenceState_(Enum):
    """The presence states. (Trailing underscore avoids clashing with the
    ``PresenceState`` *dataclass* below, which is the public value type.)"""

    OFFLINE = "offline"
    SEATED = "seated"
    IDLE = "idle"
    SIDE_HUSTLE = "side_hustle"
    VICE = "vice"
    POOL = "pool"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Public alias — callers refer to ``Presence.SEATED`` etc.
Presence = PresenceState_


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class PresenceEvent(Enum):
    """The events that drive presence transitions.

    These are the *causes* a caller supplies; the machine maps (state, event) to
    the next state. Event names are coordinator-level (sit / leave / …), matching
    the lifecycle-event vocabulary in the design's §5.3 table.
    """

    SIT = "sit"                    # take a seat at a table (from OFFLINE/IDLE/POOL)
    LEAVE = "leave"               # voluntary leave / cash-out from a seat
    RESEAT = "reseat"             # idle -> seated (re-entry from the idle pool)
    START_HUSTLE = "start_hustle"  # idle -> side hustle (AI off-grid earning)
    START_VICE = "start_vice"     # idle -> vice (AI off-grid spending)
    END_OFFGRID = "end_offgrid"   # hustle/vice timer ends -> back to idle
    GO_OFFLINE = "go_offline"     # explicit departure from the sandbox
    SEED = "seed"                 # provision a pool-funded AI into the sandbox
    RETURN_TO_POOL = "return_to_pool"  # pool-funded AI leaves a seat back to POOL


# ---------------------------------------------------------------------------
# Legal transition table (the spec)
# ---------------------------------------------------------------------------
#
# (from_state, event) -> to_state. Anything not in this map is illegal and
# raises. This is the single source of truth for "what moves are allowed", and
# is deliberately explicit so the forbidden contradictions are visible by their
# *absence*:
#
#   * There is no edge that lands you in two states — every value is a single
#     PresenceState_, so seated_and_idle / double_seat cannot be expressed.
#   * SEATED can only be reached via SIT or RESEAT, and only ever holds one
#     (table_id, seat_index); SIT-from-SEATED is NOT legal, so an entity cannot
#     be re-seated at a second table without first LEAVE-ing the first.

LEGAL_TRANSITIONS: Dict[Tuple[PresenceState_, PresenceEvent], PresenceState_] = {
    # --- Entering the sandbox / taking a seat -----------------------------
    (Presence.OFFLINE, PresenceEvent.SIT): Presence.SEATED,
    (Presence.OFFLINE, PresenceEvent.SEED): Presence.POOL,
    (Presence.POOL, PresenceEvent.SIT): Presence.SEATED,
    (Presence.IDLE, PresenceEvent.SIT): Presence.SEATED,
    (Presence.IDLE, PresenceEvent.RESEAT): Presence.SEATED,

    # --- Leaving a seat ---------------------------------------------------
    (Presence.SEATED, PresenceEvent.LEAVE): Presence.IDLE,
    (Presence.SEATED, PresenceEvent.GO_OFFLINE): Presence.OFFLINE,
    (Presence.SEATED, PresenceEvent.RETURN_TO_POOL): Presence.POOL,

    # --- Off-grid (AI side hustle / vice) ---------------------------------
    (Presence.IDLE, PresenceEvent.START_HUSTLE): Presence.SIDE_HUSTLE,
    (Presence.IDLE, PresenceEvent.START_VICE): Presence.VICE,
    (Presence.SIDE_HUSTLE, PresenceEvent.END_OFFGRID): Presence.IDLE,
    (Presence.VICE, PresenceEvent.END_OFFGRID): Presence.IDLE,

    # --- Idle / pool departures ------------------------------------------
    (Presence.IDLE, PresenceEvent.GO_OFFLINE): Presence.OFFLINE,
    (Presence.POOL, PresenceEvent.GO_OFFLINE): Presence.OFFLINE,
    (Presence.POOL, PresenceEvent.RETURN_TO_POOL): Presence.POOL,  # idempotent re-seed cleanup
}


# Events that REQUIRE a (table_id, seat_index) target, and events that must
# CLEAR it. Used by transition() to keep the seat fields consistent with the
# state — another structural guard against "SEATED with no seat" / "IDLE that
# still claims a seat".
_SEAT_REQUIRING_EVENTS: FrozenSet[PresenceEvent] = frozenset(
    {PresenceEvent.SIT, PresenceEvent.RESEAT}
)
_SEAT_CLEARING_EVENTS: FrozenSet[PresenceEvent] = frozenset(
    {
        PresenceEvent.LEAVE,
        PresenceEvent.GO_OFFLINE,
        PresenceEvent.RETURN_TO_POOL,
        PresenceEvent.START_HUSTLE,
        PresenceEvent.START_VICE,
        PresenceEvent.END_OFFGRID,
        PresenceEvent.SEED,
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IllegalPresenceTransition(Exception):
    """Raised when ``transition`` is asked for an edge not in
    ``LEGAL_TRANSITIONS`` (or when a transition's seat arguments are
    inconsistent with the event)."""


# ---------------------------------------------------------------------------
# The value type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PresenceState:
    """Immutable presence value for one ``(entity_id, sandbox_id)``.

    ``table_id`` / ``seat_index`` are populated **iff** ``state is SEATED``.
    That invariant is enforced by ``transition`` and validated in
    ``__post_init__`` so an inconsistent value cannot be constructed.

    ``updated_at`` is an opaque caller-supplied marker (ISO-8601 string, epoch,
    or ``None``); the pure machine never reads a clock. The persistence layer
    sets it.
    """

    entity_id: str
    sandbox_id: str
    state: PresenceState_
    table_id: Optional[str] = None
    seat_index: Optional[int] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        if self.state is Presence.SEATED:
            if self.table_id is None or self.seat_index is None:
                raise IllegalPresenceTransition(
                    f"SEATED presence for {self.entity_id!r}@{self.sandbox_id!r} "
                    f"requires both table_id and seat_index "
                    f"(got table_id={self.table_id!r}, seat_index={self.seat_index!r})"
                )
        else:
            if self.table_id is not None or self.seat_index is not None:
                raise IllegalPresenceTransition(
                    f"{self.state} presence for {self.entity_id!r}@{self.sandbox_id!r} "
                    f"must not carry a seat (got table_id={self.table_id!r}, "
                    f"seat_index={self.seat_index!r}) — a non-seated entity holding a "
                    f"seat is exactly the ghost-seat bug this machine forbids"
                )

    @property
    def is_seated(self) -> bool:
        return self.state is Presence.SEATED

    @property
    def is_off_grid(self) -> bool:
        """SIDE_HUSTLE or VICE — present in the sandbox but not at a table and
        not in the idle pool."""
        return self.state in (Presence.SIDE_HUSTLE, Presence.VICE)


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def offline(entity_id: str, sandbox_id: str) -> PresenceState:
    """The default / starting presence: not present in this sandbox."""
    return PresenceState(
        entity_id=entity_id,
        sandbox_id=sandbox_id,
        state=Presence.OFFLINE,
    )


def player_entity_id(owner_id: str) -> str:
    """Build the ledger-convention entity id for a human player."""
    return f"player:{owner_id}"


def ai_entity_id(personality_id: str) -> str:
    """Build the ledger-convention entity id for an AI player."""
    return f"ai:{personality_id}"


# ---------------------------------------------------------------------------
# The pure transition function
# ---------------------------------------------------------------------------


def can_transition(current: PresenceState, event: PresenceEvent) -> bool:
    """Return whether ``(current.state, event)`` is a legal edge. Pure."""
    return (current.state, event) in LEGAL_TRANSITIONS


def transition(
    current: PresenceState,
    event: PresenceEvent,
    *,
    table_id: Optional[str] = None,
    seat_index: Optional[int] = None,
    updated_at: Optional[str] = None,
) -> PresenceState:
    """Return the new ``PresenceState`` for applying ``event`` to ``current``.

    Pure: ``current`` is never mutated. Raises ``IllegalPresenceTransition`` for
    any edge not in ``LEGAL_TRANSITIONS``, and for seat-argument mismatches
    (e.g. a ``SIT`` without a seat, or a ``LEAVE`` that tries to supply one).

    ``table_id`` / ``seat_index`` are required for seat-entering events
    (``SIT`` / ``RESEAT``) and forbidden for everything else — keeping the seat
    fields consistent with the resulting state.
    """
    key = (current.state, event)
    new_state = LEGAL_TRANSITIONS.get(key)
    if new_state is None:
        raise IllegalPresenceTransition(
            f"Illegal transition for {current.entity_id!r}@{current.sandbox_id!r}: "
            f"{current.state} --{event.value}--> (no such edge). "
            f"This contradiction is unrepresentable by design."
        )

    if event in _SEAT_REQUIRING_EVENTS:
        if table_id is None or seat_index is None:
            raise IllegalPresenceTransition(
                f"Event {event.value} requires table_id and seat_index "
                f"(got table_id={table_id!r}, seat_index={seat_index!r})"
            )
        next_table_id: Optional[str] = table_id
        next_seat_index: Optional[int] = seat_index
    else:
        if table_id is not None or seat_index is not None:
            raise IllegalPresenceTransition(
                f"Event {event.value} must not supply a seat "
                f"(got table_id={table_id!r}, seat_index={seat_index!r})"
            )
        next_table_id = None
        next_seat_index = None

    # Construct fresh; __post_init__ re-validates the seat/state invariant.
    return replace(
        current,
        state=new_state,
        table_id=next_table_id,
        seat_index=next_seat_index,
        updated_at=updated_at,
    )
