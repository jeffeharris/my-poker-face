"""The tournament orchestrator.

`TournamentDirector` sits above the per-table game loop. Each **round** it plays
one hand at every table (via a pluggable `HandResolver`), folds the results back
into the field-wide standings, then rebalances the tables (break/balance/final
table). It stops when one player holds every chip.

The director itself has no poker-engine dependency — it talks only to the
`HandResolver` interface. `FakeHandResolver` is a deterministic, chip-conserving
model used by tests and the demo CLI; `EngineHandResolver` (engine_resolver.py)
drives the real poker engine with no-LLM bots.
"""

import random
from dataclasses import dataclass
from typing import Protocol

from .blinds import BlindLevel
from .config import TournamentConfig
from .field import Elimination, TournamentField, attribute_eliminators
from .seating import Seating, SeatMove, SeatingManager, build_initial_seating


def build_initial_state(
    config: TournamentConfig,
    *,
    entries: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str], TournamentField, Seating]:
    """Build the starting field + seating for a tournament config.

    By default players are generated `P01..PNN`, each assigned an archetype
    cycled from `config.field_archetypes` — the headless director and the live
    multi-table `TournamentSession` both start from this identical world.

    Pass explicit `entries` (an ordered `player_id -> archetype/name` map) to
    build the field from REAL players instead — this is how a single-table game
    becomes a one-table tournament (the human + their chosen opponents, by name).
    Seat order follows the `entries` insertion order.
    """
    if entries is not None:
        player_ids = list(entries)
        entries = dict(entries)
    else:
        player_ids = [f"P{i + 1:02d}" for i in range(config.field_size)]
        archetypes = config.field_archetypes
        entries = {pid: archetypes[i % len(archetypes)] for i, pid in enumerate(player_ids)}
    field = TournamentField(starting_stack=config.starting_stack, entries=entries)
    seating = build_initial_seating(player_ids, config.table_size)
    return player_ids, entries, field, seating


class HandResolver(Protocol):
    """Plays exactly one hand at one table and returns the resulting stacks.

    Contract:
      - `seat_order` lists the player ids at the table, in seat order; all have
        a positive stack.
      - `stacks` maps each of those ids to its current stack.
      - returns a dict over the *same* ids; the sum MUST equal the input sum
        (chips are conserved — no rake mid-tournament). A player whose returned
        stack is 0 has busted.
    """

    def resolve(
        self,
        seat_order: list[str],
        stacks: dict[str, int],
        level: BlindLevel,
        button: int,
        seed: int,
    ) -> dict[str, int]: ...


class FakeHandResolver:
    """A deterministic, chip-conserving stand-in for the real engine.

    Model: every seated player posts the big blind into a pot; the pot is awarded
    to one player chosen at random, weighted by stack so chip leaders win more
    often and the tournament converges. Short stacks bleak the blind each hand
    and bust over time. Fully reproducible from the per-hand seed.

    This is not poker — it exists so the orchestration, seating, and standings
    logic can be tested and demoed without the poker engine or any LLM.
    """

    def resolve(
        self,
        seat_order: list[str],
        stacks: dict[str, int],
        level: BlindLevel,
        button: int,
        seed: int,
    ) -> dict[str, int]:
        rng = random.Random(seed)
        bb = level.big_blind
        new = dict(stacks)
        pot = 0
        for pid in seat_order:
            contribution = min(new[pid], bb)
            new[pid] -= contribution
            pot += contribution
        if pot:
            weights = [max(1, new[pid]) for pid in seat_order]
            winner = rng.choices(seat_order, weights=weights, k=1)[0]
            new[winner] += pot
        return new


@dataclass(frozen=True)
class Standing:
    """One player's final placement."""

    player_id: str
    archetype: str
    finishing_position: int


# Terminal reasons (mirrors experiments/sng_runner.py accounting).
TERMINAL_WINNER = 'winner'  # clean finish — one player holds every chip
TERMINAL_MAX_ROUNDS = 'max_rounds'  # hit the safety cap with >1 left (pathological)


@dataclass(frozen=True)
class RoundReport:
    """What happened in one round — the raw material for the activity ticker.

    Captures the blind level in effect, who was eliminated (with finishing
    position and eliminator), and every seat move the rebalance produced (table
    breaks/balancing). Later phases turn these into "Table 6 broke",
    "You've been moved", and knockout/pay-jump beats.
    """

    round_index: int
    level: BlindLevel
    eliminations: tuple[Elimination, ...]
    seat_moves: tuple[SeatMove, ...]


@dataclass(frozen=True)
class TournamentResult:
    """The outcome of a completed tournament."""

    winner: str | None
    standings: tuple[Standing, ...]
    rounds_played: int
    terminal_reason: str
    total_chips: int


class TournamentDirector:
    """Runs a headless multi-table tournament to completion."""

    def __init__(self, config: TournamentConfig, resolver: HandResolver | None = None):
        self.config = config
        self.resolver: HandResolver = resolver or FakeHandResolver()
        self.schedule = config.blind_schedule()
        self.seating_manager = SeatingManager()

        _player_ids, self.entries, self.field, self.seating = build_initial_state(config)
        self.rounds_played = 0
        self.round_reports: list[RoundReport] = []

    # ── public API ────────────────────────────────────────────────────────────

    def run(self) -> TournamentResult:
        """Play the whole tournament and return the result."""
        self.field.assert_conservation()
        while not self.field.is_complete():
            if self.rounds_played >= self.config.max_rounds:
                return self._result(TERMINAL_MAX_ROUNDS)
            level = self.schedule.level_for_round(self.rounds_played)
            eliminations = self._play_round(level)
            self.field.assert_conservation()
            seat_moves = self.seating_manager.rebalance(self.seating)
            self.round_reports.append(
                RoundReport(
                    round_index=self.rounds_played,
                    level=level,
                    eliminations=tuple(eliminations),
                    seat_moves=tuple(seat_moves),
                )
            )
            self.rounds_played += 1
        return self._result(TERMINAL_WINNER)

    # ── internals ──────────────────────────────────────────────────────────────

    def _play_round(self, level: BlindLevel) -> list[Elimination]:
        """Play one hand at every eligible table, update stacks, record busts.

        Returns the eliminations recorded this round (for the round report).
        """
        pre_round_stacks = dict(self.field.stacks)
        table_of_player: dict[str, int] = {}  # pid -> table this round
        gains_by_table: dict[int, dict[str, int]] = {}  # table_id -> {pid: chip gain}

        for table in self.seating.tables:
            seat_order = table.players  # occupied seats in seat order; all have chips
            for pid in seat_order:
                table_of_player[pid] = table.table_id
            if len(seat_order) < 2:
                continue  # can't play a hand; consolidation will fix short tables
            seed = self._hand_seed(table.table_id)
            stacks = {pid: self.field.stacks[pid] for pid in seat_order}
            result = self.resolver.resolve(
                seat_order=seat_order,
                stacks=stacks,
                level=level,
                button=table.dealer_index_in_occupied(),
                seed=seed,
            )
            self._apply_table_result(stacks, result)
            gains_by_table[table.table_id] = {pid: result[pid] - stacks[pid] for pid in seat_order}
            for pid, new_stack in result.items():
                self.field.stacks[pid] = new_stack
            table.advance_button()

        busted = [
            (pid, pre_round_stacks[pid])
            for pid in self.field.active_ids()
            if self.field.stacks[pid] <= 0
        ]
        eliminators = attribute_eliminators(busted, table_of_player, gains_by_table)
        events = self.field.record_eliminations(busted, self.rounds_played, eliminators)

        busted_ids = {pid for pid, _ in busted}
        if busted_ids:
            for table in self.seating.tables:
                for pid in [p for p in table.players if p in busted_ids]:
                    table.remove(pid)
        return events

    def _apply_table_result(self, before: dict[str, int], after: dict[str, int]) -> None:
        """Guard the resolver contract: same players, chips conserved per table."""
        if set(before) != set(after):
            raise AssertionError("HandResolver changed the set of players at the table")
        if sum(before.values()) != sum(after.values()):
            raise AssertionError(
                f"HandResolver did not conserve chips: in={sum(before.values())} "
                f"out={sum(after.values())}"
            )

    def _hand_seed(self, table_id: int) -> int:
        """Reproducible per-(round, table) seed derived from the master seed."""
        return self.config.seed * 1_000_003 + self.rounds_played * 1009 + table_id

    def _result(self, terminal_reason: str) -> TournamentResult:
        standings = self._standings()
        winner = standings[0].player_id if standings and terminal_reason == TERMINAL_WINNER else None
        return TournamentResult(
            winner=winner,
            standings=tuple(standings),
            rounds_played=self.rounds_played,
            terminal_reason=terminal_reason,
            total_chips=self.config.total_chips,
        )

    def _standings(self) -> list[Standing]:
        """Final standings, best (position 1) first.

        Survivors (1 at a clean finish, possibly several on a max-rounds
        fallback) take the top positions ordered by current stack; eliminated
        players follow in finishing-position order.
        """
        survivors = sorted(self.field.active_ids(), key=lambda p: -self.field.stacks[p])
        standings: list[Standing] = []
        for pos, pid in enumerate(survivors, start=1):
            standings.append(Standing(pid, self.entries[pid], pos))
        for event in sorted(self.field.eliminations, key=lambda e: e.finishing_position):
            standings.append(
                Standing(event.player_id, self.entries[event.player_id], event.finishing_position)
            )
        return standings
