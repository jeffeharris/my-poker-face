"""Table seating, balancing, and breaking — the core of a multi-table tournament.

This module is pure data: it has zero dependency on the poker engine. A `Seating`
is a list of `Table`s; each table has **fixed seat positions** (a `seats` array of
length `table_size`, `None` = empty) and a dealer **button** that is a seat index.
`SeatingManager.rebalance` applies the classic MTT rules — break tables as the
field shrinks, keep table sizes within one of each other, and collapse to a single
final table — returning the explicit `SeatMove`s it made (so they can be narrated
on the activity ticker and shown in the standings view).

Realism notes:
  - Seats are physical positions, so a UI can render seat 1..N and a player keeps
    their seat until moved. Incoming players take the lowest open seat.
  - The button is a seat index and moves **forward to the next occupied seat** each
    hand. This is faithful seat-based rotation; it is not the full casino
    "dead button" rule (the button never rests on an empty seat here), which the
    engine's blind model — small/big blind derived from the dealer index over the
    seated players — does not need. Good enough for v1; revisit if exact dead-button
    blind posting ever matters.
"""

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class SeatMove:
    """A single player relocation produced by rebalancing."""

    player_id: str
    from_table: int
    to_table: int


@dataclass
class Table:
    """One tournament table: fixed seat positions + the dealer button seat."""

    table_id: int
    seats: list[str | None]
    button: int = 0

    @property
    def size(self) -> int:
        return sum(1 for s in self.seats if s is not None)

    @property
    def capacity(self) -> int:
        return len(self.seats)

    @property
    def players(self) -> list[str]:
        """Seated players in seat order (lowest seat index first)."""
        return [s for s in self.seats if s is not None]

    def occupied_indices(self) -> list[int]:
        return [i for i, s in enumerate(self.seats) if s is not None]

    def dealer_index_in_occupied(self) -> int:
        """Position of the button within `players` (the index a poker hand built
        from `players` should use as its dealer). If the button currently rests on
        an empty seat (a player just left it), snap forward to the next occupied
        seat."""
        occupied = self.occupied_indices()
        if not occupied:
            return 0
        if self.button in occupied:
            return occupied.index(self.button)
        n = len(self.seats)
        for step in range(1, n + 1):
            j = (self.button + step) % n
            if self.seats[j] is not None:
                return occupied.index(j)
        return 0

    def advance_button(self) -> None:
        """Move the button forward to the next occupied seat (after each hand)."""
        n = len(self.seats)
        for step in range(1, n + 1):
            j = (self.button + step) % n
            if self.seats[j] is not None:
                self.button = j
                return

    def first_open_seat(self) -> int | None:
        for i, s in enumerate(self.seats):
            if s is None:
                return i
        return None

    def add(self, player_id: str) -> int:
        """Seat a player in the lowest open seat; returns the seat index."""
        seat = self.first_open_seat()
        if seat is None:
            raise ValueError(f"table {self.table_id} is full")
        self.seats[seat] = player_id
        return seat

    def remove(self, player_id: str) -> None:
        for i, s in enumerate(self.seats):
            if s == player_id:
                self.seats[i] = None
                return
        raise ValueError(f"{player_id} is not seated at table {self.table_id}")

    def to_dict(self) -> dict:
        return {'table_id': self.table_id, 'seats': list(self.seats), 'button': self.button}

    @classmethod
    def from_dict(cls, d: dict) -> 'Table':
        return cls(table_id=d['table_id'], seats=list(d['seats']), button=d['button'])


@dataclass
class Seating:
    """The full set of tables in a tournament."""

    tables: list[Table]
    table_size: int

    @property
    def total_players(self) -> int:
        return sum(t.size for t in self.tables)

    def all_player_ids(self) -> list[str]:
        return [pid for t in self.tables for pid in t.players]

    def table_for(self, player_id: str) -> Table | None:
        for t in self.tables:
            if player_id in t.players:
                return t
        return None

    def to_dict(self) -> dict:
        return {'tables': [t.to_dict() for t in self.tables], 'table_size': self.table_size}

    @classmethod
    def from_dict(cls, d: dict) -> 'Seating':
        return cls(
            tables=[Table.from_dict(t) for t in d['tables']],
            table_size=d['table_size'],
        )


def build_initial_seating(player_ids: list[str], table_size: int) -> Seating:
    """Distribute players across the minimum number of tables, as evenly as
    possible (round-robin), so starting table sizes differ by at most one. The
    button starts on each table's first occupied seat."""
    if table_size < 2:
        raise ValueError("table_size must be >= 2")
    n = len(player_ids)
    if n < 2:
        raise ValueError("need at least 2 players")
    num_tables = max(1, ceil(n / table_size))
    tables = [Table(table_id=i + 1, seats=[None] * table_size, button=0) for i in range(num_tables)]
    for idx, pid in enumerate(player_ids):
        tables[idx % num_tables].add(pid)
    for t in tables:
        occupied = t.occupied_indices()
        t.button = occupied[0] if occupied else 0
    return Seating(tables=tables, table_size=table_size)


class SeatingManager:
    """Stateless application of MTT table-management rules to a `Seating`."""

    def rebalance(self, seating: Seating) -> list[SeatMove]:
        """Bring the seating to a valid post-elimination shape and return the
        moves made.

        Order of operations:
          1. Drop empty tables.
          2. Final table: if everyone fits on one table, collapse to it.
          3. Otherwise break down to the target number of tables, then balance
             remaining tables to within one player of each other.
        """
        moves: list[SeatMove] = []

        # 1. Drop empty tables (their ids retire).
        seating.tables = [t for t in seating.tables if t.size > 0]
        if not seating.tables:
            return moves

        total = seating.total_players

        # 2. Final table consolidation.
        if total <= seating.table_size:
            moves += self._consolidate_final_table(seating)
            return moves

        # 3. Break down to the target table count, then balance.
        target_tables = ceil(total / seating.table_size)
        guard = 0
        while len(seating.tables) > target_tables:
            moves += self._break_smallest(seating)
            guard += 1
            if guard > len(seating.tables) + total:  # paranoia; never expected
                break
        moves += self._balance(seating)
        return moves

    # ── internal rules ───────────────────────────────────────────────────────

    def _break_smallest(self, seating: Seating) -> list[SeatMove]:
        """Break the smallest table, redistributing its players onto the other
        tables (filling the emptiest table first to keep sizes even)."""
        moves: list[SeatMove] = []
        break_table = min(seating.tables, key=lambda t: (t.size, -t.table_id))
        movers = list(break_table.players)
        seating.tables.remove(break_table)
        for pid in movers:
            dest = min(seating.tables, key=lambda t: (t.size, t.table_id))
            dest.add(pid)
            moves.append(SeatMove(pid, break_table.table_id, dest.table_id))
        return moves

    def _balance(self, seating: Seating) -> list[SeatMove]:
        """Move single players from the largest table to the smallest until no
        two tables differ by more than one player."""
        moves: list[SeatMove] = []
        guard = 0
        while len(seating.tables) > 1:
            biggest = max(seating.tables, key=lambda t: (t.size, t.table_id))
            smallest = min(seating.tables, key=lambda t: (t.size, t.table_id))
            if biggest.size - smallest.size <= 1:
                break
            # Move the player in the highest-index occupied seat — a simple,
            # deterministic choice for v1.
            pid = biggest.players[-1]
            biggest.remove(pid)
            smallest.add(pid)
            moves.append(SeatMove(pid, biggest.table_id, smallest.table_id))
            guard += 1
            if guard > seating.total_players:  # paranoia; loop always converges
                break
        return moves

    def _consolidate_final_table(self, seating: Seating) -> list[SeatMove]:
        """Collapse everyone onto a single final table (the largest survives)."""
        moves: list[SeatMove] = []
        dest = max(seating.tables, key=lambda t: (t.size, -t.table_id))
        for t in list(seating.tables):
            if t is dest:
                continue
            for pid in list(t.players):
                dest.add(pid)
                moves.append(SeatMove(pid, t.table_id, dest.table_id))
            seating.tables.remove(t)
        return moves
