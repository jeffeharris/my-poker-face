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
from .field import TournamentField
from .seating import SeatingManager, build_initial_seating


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

        player_ids = [f"P{i + 1:02d}" for i in range(config.field_size)]
        archetypes = config.field_archetypes
        self.entries = {
            pid: archetypes[i % len(archetypes)] for i, pid in enumerate(player_ids)
        }
        self.field = TournamentField(
            starting_stack=config.starting_stack, entries=self.entries
        )
        self.seating = build_initial_seating(player_ids, config.table_size)
        self.rounds_played = 0

    # ── public API ────────────────────────────────────────────────────────────

    def run(self) -> TournamentResult:
        """Play the whole tournament and return the result."""
        self.field.assert_conservation()
        while not self.field.is_complete():
            if self.rounds_played >= self.config.max_rounds:
                return self._result(TERMINAL_MAX_ROUNDS)
            level = self.schedule.level_for_round(self.rounds_played)
            self._play_round(level)
            self.field.assert_conservation()
            self.seating_manager.rebalance(self.seating)
            self.rounds_played += 1
        return self._result(TERMINAL_WINNER)

    # ── internals ──────────────────────────────────────────────────────────────

    def _play_round(self, level: BlindLevel) -> None:
        """Play one hand at every eligible table, update stacks, record busts."""
        pre_round_stacks = dict(self.field.stacks)

        for table in self.seating.tables:
            seat_order = [pid for pid in table.players if self.field.stacks.get(pid, 0) > 0]
            if len(seat_order) < 2:
                continue  # can't play a hand; consolidation will fix short tables
            seed = self._hand_seed(table.table_id)
            stacks = {pid: self.field.stacks[pid] for pid in seat_order}
            result = self.resolver.resolve(
                seat_order=seat_order,
                stacks=stacks,
                level=level,
                button=table.button % len(seat_order),
                seed=seed,
            )
            self._apply_table_result(stacks, result)
            for pid, new_stack in result.items():
                self.field.stacks[pid] = new_stack
            table.advance_button()

        busted = [
            (pid, pre_round_stacks[pid])
            for pid in self.field.active_ids()
            if self.field.stacks[pid] <= 0
        ]
        self.field.record_eliminations(busted, self.rounds_played)

        busted_ids = {pid for pid, _ in busted}
        if busted_ids:
            for table in self.seating.tables:
                for pid in [p for p in table.players if p in busted_ids]:
                    table.remove(pid)

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
