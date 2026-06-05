"""Field-wide standings and elimination tracking for a multi-table tournament.

This is the multi-table generalization of `poker/tournament_tracker.py` (which
tracks a single table). It is pure data — no poker engine — and owns:

  - the live chip count for every player still in the tournament,
  - the elimination log with **global** finishing positions (position counts the
    whole field, not one table),
  - the chip-conservation invariant.

Finishing position rule: when a batch of players busts in the same round, they
take the bottom N positions; ties are broken by **stack at the start of the
hand** — the player who came in with more chips finishes higher. This matches
how a live MTT ranks two players who bust on the same hand and is pinned here so
headless and (future) live paths agree.
"""

from dataclasses import dataclass, field as dataclass_field


@dataclass(frozen=True)
class Elimination:
    """One player's exit from the tournament."""

    player_id: str
    finishing_position: int
    round_index: int
    eliminator: str | None = None

    def to_dict(self) -> dict:
        return {
            'player_id': self.player_id,
            'finishing_position': self.finishing_position,
            'round_index': self.round_index,
            'eliminator': self.eliminator,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'Elimination':
        return cls(
            player_id=d['player_id'],
            finishing_position=d['finishing_position'],
            round_index=d['round_index'],
            eliminator=d.get('eliminator'),
        )


def attribute_eliminators(
    busted: list[tuple[str, int]],
    table_of_player: dict[str, int],
    gains_by_table: dict[int, dict[str, int]],
) -> dict[str, str]:
    """Best-effort eliminator per busted player: the biggest live chip-gainer at
    their table (the player who won the pot they died in).

    A heuristic — in a multiway pot the largest gainer is the most likely
    knockout — but resolver-agnostic and good enough for v1 standings/prestige.
    `gains_by_table[table_id][player_id]` is the net chip change over the round.
    """
    busted_ids = {pid for pid, _ in busted}
    eliminators: dict[str, str] = {}
    for pid, _ in busted:
        gains = gains_by_table.get(table_of_player.get(pid), {})
        winners = {p: g for p, g in gains.items() if p not in busted_ids and g > 0}
        if winners:
            eliminators[pid] = max(winners, key=lambda p: winners[p])
    return eliminators


@dataclass
class TournamentField:
    """Authoritative chip counts + standings for the whole field."""

    starting_stack: int
    entries: dict[str, str]  # player_id -> archetype/name
    stacks: dict[str, int] = dataclass_field(default_factory=dict)
    eliminations: list[Elimination] = dataclass_field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.stacks:
            self.stacks = {pid: self.starting_stack for pid in self.entries}

    @property
    def field_size(self) -> int:
        return len(self.entries)

    @property
    def total_chips(self) -> int:
        return self.field_size * self.starting_stack

    @property
    def active_count(self) -> int:
        return len(self.stacks)

    def active_ids(self) -> list[str]:
        return list(self.stacks)

    def is_complete(self) -> bool:
        return len(self.stacks) <= 1

    def winner(self) -> str | None:
        if len(self.stacks) == 1:
            return next(iter(self.stacks))
        return None

    def chip_sum(self) -> int:
        """Sum of all live stacks. Busted players hold 0 (and are removed), so
        this equals `total_chips` at every round boundary — the conservation
        invariant."""
        return sum(self.stacks.values())

    def assert_conservation(self) -> None:
        actual = self.chip_sum()
        if actual != self.total_chips:
            raise AssertionError(
                f"chip conservation violated: sum(stacks)={actual} "
                f"!= field_size*starting_stack={self.total_chips}"
            )

    def record_eliminations(
        self,
        busted: list[tuple[str, int]],
        round_index: int,
        eliminators: dict[str, str] | None = None,
    ) -> list[Elimination]:
        """Record players who busted this round.

        `busted` is a list of (player_id, stack_at_hand_start). Positions are
        assigned from the current remaining count downward, worst (smallest
        starting stack) first. Busted players are removed from `stacks`.
        """
        if not busted:
            return []
        eliminators = eliminators or {}
        remaining_before = len(self.stacks)
        # Smallest starting stack busts "first" → takes the worst position.
        ordered = sorted(busted, key=lambda b: b[1])
        events: list[Elimination] = []
        position = remaining_before
        for player_id, _start_stack in ordered:
            event = Elimination(
                player_id=player_id,
                finishing_position=position,
                round_index=round_index,
                eliminator=eliminators.get(player_id),
            )
            self.eliminations.append(event)
            events.append(event)
            position -= 1
            self.stacks.pop(player_id, None)
        return events

    # ── serialization (for tournament persistence) ──────────────────────────────

    def to_dict(self) -> dict:
        return {
            'starting_stack': self.starting_stack,
            'entries': dict(self.entries),
            'stacks': dict(self.stacks),
            'eliminations': [e.to_dict() for e in self.eliminations],
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'TournamentField':
        return cls(
            starting_stack=d['starting_stack'],
            entries=dict(d['entries']),
            stacks=dict(d['stacks']),
            eliminations=[Elimination.from_dict(e) for e in d['eliminations']],
        )
