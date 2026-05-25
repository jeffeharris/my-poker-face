"""CashTable — the per-table state object for cash mode.

A `CashTable` represents one seated table at a fixed stake. v1 has
exactly one of these at a time (single-table foundation); v2 will
hold many in a lobby. The shape is identical either way — the spec's
"tables are first-class objects" invariant ensures v2 doesn't need a
redesign.

State is immutable. Mutations return new instances via `dataclasses.replace`
or explicit helpers. The session layer persists the cash table's
bankroll-side effects (player_bankroll_state and ai_bankroll_state writes)
through the BankrollRepository; the CashTable itself is in-memory only
in v1 — table identity comes back from the session config on restart,
not from a DB row.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2 §"Data model".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Tuple

# Sentinel used in `seats` for the human player. v1 supports exactly
# one human per table; v2 (multi-human) would generalize this to a
# `player:<player_id>` prefix or move to a tagged union. For now,
# unique player resolution lives in the session layer.
PLAYER_SEAT_ID = "player"


@dataclass(frozen=True)
class CashTable:
    """Immutable cash-table state.

    `seats` is a fixed-length tuple of length `seat_count`; entries
    are `None` (empty), `PLAYER_SEAT_ID` ("player"), or an AI
    personality_id. `stacks` maps the same seat-id string to the
    player's current table stack in chips. An empty seat has no
    entry in `stacks` (not a 0-valued entry — keeps the "no seat
    here" and "seat here with zero chips" cases distinguishable;
    the latter happens transiently in partial-all-in survival).

    `hand_in_progress` blocks sit/leave/topup per spec §"Sit / leave
    rules". The session layer flips this on at hand start and off at
    hand settlement.
    """

    table_id: str
    stake_label: str
    big_blind: int
    min_buy_in: int
    max_buy_in: int
    seat_count: int
    seats: Tuple[Optional[str], ...] = field(default_factory=tuple)
    stacks: Mapping[str, int] = field(default_factory=dict)
    hand_in_progress: bool = False

    def __post_init__(self) -> None:
        if not self.seats:
            object.__setattr__(self, "seats", tuple([None] * self.seat_count))
        if len(self.seats) != self.seat_count:
            raise ValueError(
                f"seats length ({len(self.seats)}) does not match "
                f"seat_count ({self.seat_count})"
            )
        # Freeze the stacks mapping so accidental mutation raises
        # rather than corrupting state silently.
        if not isinstance(self.stacks, dict):
            return
        object.__setattr__(self, "stacks", dict(self.stacks))

    # --- read helpers ---

    def seat_index_of(self, seat_id: str) -> Optional[int]:
        """Return the index of the seat occupied by `seat_id`, or None."""
        for i, occupant in enumerate(self.seats):
            if occupant == seat_id:
                return i
        return None

    def is_seated(self, seat_id: str) -> bool:
        return self.seat_index_of(seat_id) is not None

    def open_seats(self) -> Tuple[int, ...]:
        return tuple(i for i, occupant in enumerate(self.seats) if occupant is None)

    def stack_of(self, seat_id: str) -> int:
        """Chips on the table for the given seat. 0 for an empty seat
        OR a seat present in `seats` with no `stacks` entry yet.
        """
        return self.stacks.get(seat_id, 0)

    # --- functional updates ---

    def with_seat(self, seat_index: int, seat_id: Optional[str]) -> CashTable:
        """Return a copy with `seat_index` set to `seat_id`.

        Used by sit_down (assigns the new occupant) and leave/bust
        (clears with None). Does NOT touch `stacks` — callers manage
        the stack mapping explicitly so the two updates compose
        cleanly in atomic transitions.
        """
        if seat_index < 0 or seat_index >= self.seat_count:
            raise ValueError(f"seat_index {seat_index} out of range")
        new_seats = tuple(
            seat_id if i == seat_index else occupant for i, occupant in enumerate(self.seats)
        )
        return replace(self, seats=new_seats)

    def with_stack(self, seat_id: str, chips: int) -> CashTable:
        """Return a copy with the seat's stack set to `chips`.

        `chips == 0` writes a zero entry rather than deleting the key.
        Use `without_stack` to remove the entry entirely (called when
        a seat is freed).
        """
        new_stacks = dict(self.stacks)
        new_stacks[seat_id] = chips
        return replace(self, stacks=new_stacks)

    def without_stack(self, seat_id: str) -> CashTable:
        """Return a copy with the seat's stack entry removed."""
        if seat_id not in self.stacks:
            return self
        new_stacks = dict(self.stacks)
        del new_stacks[seat_id]
        return replace(self, stacks=new_stacks)

    def with_hand_in_progress(self, in_progress: bool) -> CashTable:
        return replace(self, hand_in_progress=in_progress)


def new_table(
    *,
    table_id: str,
    stake_label: str,
    big_blind: int,
    seat_count: int = 6,
    min_buy_in_bb: int = 40,
    max_buy_in_bb: int = 100,
) -> CashTable:
    """Construct an empty cash table with stake-derived buy-in caps.

    Default seat_count is 6 (the only v1 size — six-max poker per
    NEXT_PHASE_VISION). v2 may add 9-max; the field is parameterized
    so the change is config-only.

    Buy-in bounds default to 40bb / 100bb, the standard online cash
    cap pair. They're explicit parameters so per-stake overrides
    (looser or tighter caps) drop in without code changes.
    """
    return CashTable(
        table_id=table_id,
        stake_label=stake_label,
        big_blind=big_blind,
        min_buy_in=big_blind * min_buy_in_bb,
        max_buy_in=big_blind * max_buy_in_bb,
        seat_count=seat_count,
    )
