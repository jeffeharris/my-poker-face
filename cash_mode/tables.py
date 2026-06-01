"""Persistent cash-table state — the lobby's data substrate.

Distinct from `cash_mode.table.CashTable`, which is the legacy in-memory
per-session table object. `CashTableState` is the *persisted* lobby
table — one row per stake in v1.5 — with explicit seat slots that
carry across player sessions.

Each `CashTableState` has 6 slots (4 AI baseline + 2 open) encoded as
typed slot dicts in `seats`. Slot kinds:

  - `"open"`  — empty seat, eligible for the player or a live-fill AI.
  - `"ai"`    — `{"kind": "ai", "personality_id": str, "chips": int}`
  - `"human"` — `{"kind": "human", "personality_id": owner_id, "chips": int}`
                (set transiently while the player is seated; reverts
                to `"open"` on leave).
  - `"reserved"` — `{"kind": "reserved", "personality_id": owner_id,
                "reserved_at": iso, "expire_at": iso}` — a short-lived
                hold placed when a player taps a seat they can only
                afford via sponsorship, so the world ticker's live-fill
                can't seat an AI in it while the SponsorModal is open.
                Resolves to `"human"` on sponsor-accept, back to `"open"`
                on modal-close (explicit release) or TTL expiry (swept by
                `refresh_unseated_tables`). Distinct from `"human"` so the
                expiry sweep can target abandoned holds without any risk
                of evicting a genuinely seated player.

Per-seat chips are persisted: an AI who wins big keeps those chips for
the next player who sits down (or, per movement rules, may stake-up
and take the chips with them).

AUTHORITY NOTE (chip-custody + presence cutovers, 2026-06-01):
`seats` is a CACHE/working store, not authoritative truth.
  - **Occupancy** (who is seated / idle) is owned by `entity_presence`
    (`PRESENCE_AUTHORITY_ENABLED`). The `seats` occupancy half is a
    projection still written until the read-side demotion.
  - **Committed chips** (bankroll / at-seat custody) are owned by the
    ledger (`chip_ledger_entries`, `balance_of`); `seats[].chips` is the
    LIVE in-hand stack — the one fact that legitimately lives here (the
    ledger doesn't track per-hand P&L).
Do NOT treat `seats` occupancy or a bankroll int as authoritative, and
do NOT add a new reconciler to repair `seats` vs another store — finish
the matching demotion instead. Full register + retirement gates:
`docs/plans/CASH_MODE_TECH_DEBT.md`.

Spec: `docs/plans/CASH_MODE_LOBBY_HANDOFF.md` §"Persistent table state".
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


TABLE_SEAT_COUNT = 6
BASELINE_AI_SEATS = 4
OPEN_SEATS = 2

# How long a sponsorship seat-hold survives before the lobby refresh
# sweeps it back to `"open"`. Long enough that a player can read the
# SponsorModal's offers and pick a lender; short enough that an
# abandoned modal (closed tab, dropped network) doesn't strand the seat
# against AI live-fill for more than a couple minutes. The frontend
# releases explicitly on modal-close, so this is the safety net for the
# cases where that call never arrives.
SEAT_RESERVATION_TTL_SECONDS = 120


def open_slot() -> Dict[str, Any]:
    """Return a fresh `"open"` slot dict.

    Slot dicts are plain dicts (not dataclasses) because they're
    serialized to/from JSON as the table's `seats_json` column. Using
    a constructor function keeps the canonical shape obvious at call
    sites.
    """
    return {"kind": "open"}


def ai_slot(personality_id: str, chips: int) -> Dict[str, Any]:
    """Return an AI slot dict."""
    return {"kind": "ai", "personality_id": personality_id, "chips": int(chips)}


def ai_slot_fish(personality_id: str, chips: int) -> Dict[str, Any]:
    """Return an AI slot dict for a pool-funded fish at a casino.

    Identical to `ai_slot` plus an `archetype='fish'` stamp. Fish are
    real, curated personalities (e.g. `vacation_greg`) seated at
    `table_type='casino'` venues with chips drawn from the bank pool,
    not their bankroll. The stamp lets the movement and teardown paths
    identify fish seats by reading the seat dict alone — no per-tick
    `PersonalityRepository` lookup. It is the single source of truth
    for "this seat is a fish" during a hand. See
    `cash_mode/casino_provisioning.py`.
    """
    return {
        "kind": "ai",
        "personality_id": personality_id,
        "chips": int(chips),
        "archetype": "fish",
    }


def personality_for_seat(seat: Dict[str, Any], personality_repo) -> Optional[Dict[str, Any]]:
    """Resolve a seat to its personality config dict via the DB.

    Looks up `PersonalityRepository.load_personality_by_id` for AI seats
    — fish included, since they're real curated personas now (no inline
    blob). Returns None for open/human seats or when the lookup fails.

    Catches only **expected** repo failures (DB IO, corrupted JSON) and
    logs them, returning None. Programmer bugs (AttributeError, TypeError,
    schema-drift KeyError) propagate — those are caller-level mistakes
    that should crash loudly so they get fixed.
    """
    if not isinstance(seat, dict):
        return None
    pid = seat.get("personality_id")
    if not pid or personality_repo is None:
        return None
    try:
        return personality_repo.load_personality_by_id(pid)
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        logger.warning(
            "[CASH] personality_for_seat lookup failed for pid=%r: %s",
            pid,
            exc,
        )
        return None


def human_slot(owner_id: str, chips: int) -> Dict[str, Any]:
    """Return a human slot dict.

    The human's `personality_id` is the player owner_id (not a real
    personality), which lets the routing layer treat the seat uniformly
    with AI seats when checking "is this seat occupied."
    """
    return {"kind": "human", "personality_id": owner_id, "chips": int(chips)}


def reserved_slot(owner_id: str, now: datetime) -> Dict[str, Any]:
    """Return a short-lived seat-hold slot for `owner_id`.

    Placed when a player taps a seat they can only afford via
    sponsorship: it pins the seat as non-`"open"` (so the world
    ticker's live-fill skips it) while the SponsorModal is up, then
    resolves to `"human"` on accept or `"open"` on release/expiry.

    `expire_at` is stamped `SEAT_RESERVATION_TTL_SECONDS` ahead of
    `now` so the lobby refresh can reclaim abandoned holds without any
    server-side timer — the sweep just compares against the wall clock
    it already has. Both timestamps are ISO strings to match the rest
    of the seats_json payload (plain JSON, no datetime objects).
    """
    expire_at = now + timedelta(seconds=SEAT_RESERVATION_TTL_SECONDS)
    return {
        "kind": "reserved",
        "personality_id": owner_id,
        "reserved_at": now.isoformat(),
        "expire_at": expire_at.isoformat(),
    }


def is_reservation_expired(slot: Dict[str, Any], now: datetime) -> bool:
    """True if `slot` is a `"reserved"` hold whose TTL has elapsed.

    Tolerant of a missing/garbled `expire_at` (treats it as expired) so
    a malformed hold can never wedge a seat permanently — the sweep
    frees it on the next refresh.
    """
    if not isinstance(slot, dict) or slot.get("kind") != "reserved":
        return False
    raw = slot.get("expire_at")
    if not raw:
        return True
    try:
        return datetime.fromisoformat(raw) <= now
    except (ValueError, TypeError):
        return True


@dataclass
class CashTableState:
    """Persisted cash-mode table row from the `cash_tables` SQLite table.

    `seats` is a length-`TABLE_SEAT_COUNT` list of slot dicts. The list
    is intentionally mutable for ease of incremental edits in the pure
    helpers (`refresh_table_roster`, sit_down); persistence writes
    serialize the whole list to `seats_json` on save.

    `created_at` / `last_activity_at` are mostly informational; the
    lobby refresh hook bumps `last_activity_at` after movement
    decisions so admin views can see stale tables.
    """

    table_id: str
    stake_label: str
    seats: List[Dict[str, Any]] = field(default_factory=list)
    created_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    # Seat index of the dealer button on this table. Rotates clockwise
    # once per simulated hand (see cash_mode/full_sim.py). Persisted to
    # the schema-v96 `cash_tables.dealer_idx` column so the rotation
    # survives backend restart. Range [0, TABLE_SEAT_COUNT). Default 0
    # matches the schema column's DEFAULT and the pre-v96 implicit
    # behavior (first seat held the button on every refresh).
    dealer_idx: int = 0
    # v111: user-facing label ("The Lodge"). NULL → frontend falls back
    # to the stake label. Lobby tables get a name from lobby_config at
    # seed time; future private/casino tables set their own.
    name: Optional[str] = None
    # v111: discriminator for table flavor. 'lobby' = public lobby table
    # seeded from lobby_config (current behavior). Reserved values
    # 'private' (user-owned tables) and 'casino' (themed house tables)
    # are schema slots only — no flow logic for them yet.
    table_type: str = 'lobby'
    # v113: smooth-shutdown countdown for casino tables. NULL on lobby /
    # active casinos; integer N when casino is winding down (closing
    # state with N hands remaining). Decrements one-per-hand from both
    # the full-sim and human-play hand-boundary hooks until N=0, at
    # which point the next provisioning resolution deletes the row.
    closing_hand_countdown: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.seats:
            self.seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
        if len(self.seats) != TABLE_SEAT_COUNT:
            raise ValueError(
                f"seats length ({len(self.seats)}) must equal "
                f"TABLE_SEAT_COUNT ({TABLE_SEAT_COUNT})"
            )
        # Defensive: every slot must have a known kind.
        for i, slot in enumerate(self.seats):
            if not isinstance(slot, dict) or "kind" not in slot:
                raise ValueError(f"Slot {i} is malformed: {slot!r}")
            if slot["kind"] not in ("open", "ai", "human", "reserved"):
                raise ValueError(
                    f"Slot {i} has unknown kind {slot['kind']!r}; "
                    f"expected open/ai/human/reserved"
                )

    # --- read helpers ---

    def open_seat_indices(self) -> List[int]:
        """Return indices of all `"open"` seats."""
        return [i for i, s in enumerate(self.seats) if s["kind"] == "open"]

    def ai_seat_indices(self) -> List[int]:
        """Return indices of all `"ai"` seats."""
        return [i for i, s in enumerate(self.seats) if s["kind"] == "ai"]

    def human_seat_index(self) -> Optional[int]:
        """Return the human seat's index if a human is seated, else None."""
        for i, s in enumerate(self.seats):
            if s["kind"] == "human":
                return i
        return None

    def reserved_seat_index_for(self, owner_id: str) -> Optional[int]:
        """Return the index of a `"reserved"` seat held by `owner_id`, else None.

        Used to recognise a player's own active sponsorship hold so the
        sit/sponsor paths treat it as claimable-by-them rather than a
        409 "seat is not open".
        """
        for i, s in enumerate(self.seats):
            if s["kind"] == "reserved" and s.get("personality_id") == owner_id:
                return i
        return None

    def seated_personality_ids(self) -> List[str]:
        """Return the personality_ids of seated AIs (excludes human)."""
        return [
            s["personality_id"] for s in self.seats if s["kind"] == "ai" and s.get("personality_id")
        ]

    def has_open_seat(self) -> bool:
        return any(s["kind"] == "open" for s in self.seats)

    # --- functional updates ---

    def with_seat(self, index: int, slot: Dict[str, Any]) -> CashTableState:
        """Return a copy with seat `index` replaced by `slot`."""
        if index < 0 or index >= TABLE_SEAT_COUNT:
            raise ValueError(f"seat index {index} out of range")
        new_seats = list(self.seats)
        new_seats[index] = dict(slot)
        return replace(self, seats=new_seats)


# --- (de)serialization ---


def seats_to_json(seats: List[Dict[str, Any]]) -> str:
    """Serialize the seats list to a JSON string for the `seats_json` column."""
    return json.dumps(seats)


def seats_from_json(seats_json: str) -> List[Dict[str, Any]]:
    """Parse a `seats_json` column value back to a seats list.

    Raises ValueError on malformed JSON or unexpected shape — `CashTableState.__post_init__`
    further validates slot kinds.
    """
    parsed = json.loads(seats_json)
    if not isinstance(parsed, list):
        raise ValueError(f"seats_json must decode to a list, got {type(parsed).__name__}")
    return parsed


# --- Idle pool ---


# Movement decision reasons — also used as `cash_idle_pool.reason` values.
# Keep the set explicit so the lobby UI / admin views can interpret the
# state.
IDLE_REASONS: Tuple[str, ...] = (
    "forced_leave",  # AI busted or near-busted; needs bankroll recovery
    "stake_up_queued",  # AI won big and wants a higher stake
    "take_break",  # AI's choice to step away for a bit
    "bored_move",  # Small base-rate cycling to keep the lobby alive
)


@dataclass
class IdlePoolEntry:
    """One row from `cash_idle_pool` — an AI between cash sessions.

    `personality_id` is the primary key — an AI is either at a table
    or in the idle pool, never both. `target_stake` is non-None only
    when `reason == 'stake_up_queued'`; it preserves the AI's intent
    to walk up a tier on re-entry.
    """

    personality_id: str
    left_at: datetime
    reason: str
    target_stake: Optional[str] = None

    def __post_init__(self) -> None:
        if self.reason not in IDLE_REASONS:
            raise ValueError(f"Unknown reason {self.reason!r}; expected one of {IDLE_REASONS}")
