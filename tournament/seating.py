"""Table seating, balancing, and breaking — the core of a multi-table tournament.

This module is pure data: it has zero dependency on the poker engine. A `Seating`
is a list of `Table`s, each an ordered list of seated player ids plus a button
index. `SeatingManager.rebalance` applies the classic MTT rules — break tables as
the field shrinks, keep table sizes within one of each other, and collapse to a
single final table — returning the explicit `SeatMove`s it made (so they can be
narrated on the activity ticker later).

v1 simplification: a table is modelled as an ordered list of *occupied* seats
(not a fixed-size seat array), so physical seat geometry and the "dead button"
rule are not reproduced. Player counts and movements — the things that must be
correct for balancing — are exact. Seat-geometry realism is a later refinement.
"""

from dataclasses import dataclass, field
from math import ceil


@dataclass(frozen=True)
class SeatMove:
    """A single player relocation produced by rebalancing."""

    player_id: str
    from_table: int
    to_table: int


@dataclass
class Table:
    """One tournament table: occupied seats in order, plus the button index."""

    table_id: int
    players: list[str] = field(default_factory=list)
    button: int = 0

    @property
    def size(self) -> int:
        return len(self.players)

    def _clamp_button(self) -> None:
        if self.players:
            self.button %= len(self.players)
        else:
            self.button = 0

    def advance_button(self) -> None:
        """Move the button to the next seat (called after each hand)."""
        if self.players:
            self.button = (self.button + 1) % len(self.players)

    def remove(self, player_id: str) -> None:
        self.players.remove(player_id)
        self._clamp_button()

    def add(self, player_id: str) -> None:
        self.players.append(player_id)


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


def build_initial_seating(player_ids: list[str], table_size: int) -> Seating:
    """Distribute players across the minimum number of tables, as evenly as
    possible (round-robin), so starting table sizes differ by at most one."""
    if table_size < 2:
        raise ValueError("table_size must be >= 2")
    n = len(player_ids)
    if n < 2:
        raise ValueError("need at least 2 players")
    num_tables = max(1, ceil(n / table_size))
    tables = [Table(table_id=i + 1) for i in range(num_tables)]
    for idx, pid in enumerate(player_ids):
        tables[idx % num_tables].add(pid)
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
        seating.tables = [t for t in seating.tables if t.players]
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
        # Smallest table; tie broken by highest id for determinism.
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
            # Move the player just behind the button (the last seat) — a simple,
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
        dest._clamp_button()
        return moves
