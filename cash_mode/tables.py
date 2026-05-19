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

Per-seat chips are persisted: an AI who wins big keeps those chips for
the next player who sits down (or, per movement rules, may stake-up
and take the chips with them).

Spec: `docs/plans/CASH_MODE_LOBBY_HANDOFF.md` §"Persistent table state".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


TABLE_SEAT_COUNT = 6
BASELINE_AI_SEATS = 4
OPEN_SEATS = 2


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


def human_slot(owner_id: str, chips: int) -> Dict[str, Any]:
    """Return a human slot dict.

    The human's `personality_id` is the player owner_id (not a real
    personality), which lets the routing layer treat the seat uniformly
    with AI seats when checking "is this seat occupied."
    """
    return {"kind": "human", "personality_id": owner_id, "chips": int(chips)}


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
            if slot["kind"] not in ("open", "ai", "human"):
                raise ValueError(
                    f"Slot {i} has unknown kind {slot['kind']!r}; "
                    f"expected open/ai/human"
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

    def seated_personality_ids(self) -> List[str]:
        """Return the personality_ids of seated AIs (excludes human)."""
        return [
            s["personality_id"]
            for s in self.seats
            if s["kind"] == "ai" and s.get("personality_id")
        ]

    def has_open_seat(self) -> bool:
        return any(s["kind"] == "open" for s in self.seats)

    # --- functional updates ---

    def with_seat(self, index: int, slot: Dict[str, Any]) -> "CashTableState":
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
    "forced_leave",       # AI busted or near-busted; needs bankroll recovery
    "stake_up_queued",    # AI won big and wants a higher stake
    "take_break",         # AI's choice to step away for a bit
    "bored_move",         # Small base-rate cycling to keep the lobby alive
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
            raise ValueError(
                f"Unknown reason {self.reason!r}; expected one of {IDLE_REASONS}"
            )
