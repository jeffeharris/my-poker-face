"""Live-human seam for a multi-table tournament.

`TournamentSession` wraps the headless engine with one designated **human** at one
table, and encodes the two decided semantics (see the plan doc):

- **Player-gated time.** Nothing advances except when the caller invokes
  `play_round()` (one human hand). "Backing out to the standings view" is simply
  *not calling* `play_round` — so the whole field freezes. There is no background
  ticker, unlike cash/career mode.
- **0/1/2 pacing.** When the human plays one hand, every other table plays 0, 1,
  or 2 AI hands (jittered, mean 1), so the field stays loosely in sync without the
  human becoming a bottleneck or the AI tables sprinting ahead.

The session is resolver-agnostic: the human table's hand is resolved by a callback
the caller supplies — in production it bridges to the live game handler; in tests
it's a Fake/Engine resolver — and the other tables use the AI resolver. The
callback and the AI resolver share the `HandResolver.resolve` signature.

Relocating the human across table breaks needs **no special handling**: the human
is an ordinary entry in the single seating model, so `seating.table_for(human)` is
always their current table. This is the payoff of the field-as-source-of-truth
design — moving the human is the same atomic operation as moving any AI, which is
what keeps the ghost-seat bug class out of reach.
"""

import random
from typing import Callable

from .blinds import BlindLevel
from .config import TournamentConfig
from .director import RoundReport, build_initial_state
from .field import attribute_eliminators
from .seating import SeatingManager

# A jittered hand count per AI table per human hand. Weighted so the mean is
# exactly 1.0 (0+1+1+2)/4 — the field tracks the human without drifting.
PACING_CHOICES = (0, 1, 1, 2)

# Signature shared by the AI resolver's `.resolve` and the human-table callback.
HandFn = Callable[..., dict[str, int]]


class TournamentSession:
    """A multi-table tournament with one live human at one table."""

    def __init__(self, config: TournamentConfig, ai_resolver, human_id: str | None = None):
        self.config = config
        self._ai_resolve: HandFn = ai_resolver.resolve
        self.schedule = config.blind_schedule()
        self.seating_manager = SeatingManager()

        player_ids, self.entries, self.field, self.seating = build_initial_state(config)
        self.human_id = human_id or player_ids[0]
        if self.human_id not in self.entries:
            raise ValueError(f"human_id {self.human_id!r} is not in the field")

        self.rounds = 0  # blind clock + seed source; one per round
        self._hand_counter = 0  # unique per hand played (seed source)
        self.round_reports: list[RoundReport] = []
        self.field.assert_conservation()

    # ── status / views ─────────────────────────────────────────────────────────

    @property
    def human_out(self) -> bool:
        return self.human_id not in self.field.stacks

    @property
    def human_table(self):
        return self.seating.table_for(self.human_id)

    def is_complete(self) -> bool:
        return self.field.is_complete()

    def current_level(self) -> BlindLevel:
        return self.schedule.level_for_round(self.rounds)

    def winner(self) -> str | None:
        return self.field.winner()

    def human_rank(self) -> int | None:
        """The human's live place (1 = chip leader) while in, or their finishing
        position once out."""
        if self.human_out:
            for e in self.field.eliminations:
                if e.player_id == self.human_id:
                    return e.finishing_position
            return None
        hs = self.field.stacks[self.human_id]
        return 1 + sum(1 for s in self.field.stacks.values() if s > hs)

    def _table_view(self, table) -> dict:
        return {
            'table_id': table.table_id,
            'size': table.size,
            'is_human_table': self.human_id in table.players,
            'seats': [
                {
                    'seat': i,
                    'player_id': pid,
                    'stack': self.field.stacks.get(pid) if pid else None,
                    'archetype': self.entries.get(pid) if pid else None,
                    'is_human': pid == self.human_id,
                    'is_button': i == table.button and pid is not None,
                }
                for i, pid in enumerate(table.seats)
            ],
        }

    def standings_view(self, recent: int = 8) -> dict:
        """Everything the tournament standings menu renders. World is whatever it
        is right now — this is a pure read, it never advances anything."""
        level = self.current_level()
        ht = self.human_table
        return {
            'field_size': self.field.field_size,
            'players_remaining': self.field.active_count,
            'rounds': self.rounds,
            'complete': self.is_complete(),
            'winner': self.winner(),
            'level': {
                'level': level.level,
                'small_blind': level.small_blind,
                'big_blind': level.big_blind,
                'ante': level.ante,
            },
            'human': {
                'player_id': self.human_id,
                'out': self.human_out,
                'rank': self.human_rank(),
                'stack': self.field.stacks.get(self.human_id),
                'table_id': ht.table_id if ht else None,
            },
            'tables': [self._table_view(t) for t in sorted(self.seating.tables, key=lambda t: t.table_id)],
            'recent_eliminations': [
                {
                    'player_id': e.player_id,
                    'finishing_position': e.finishing_position,
                    'eliminator': e.eliminator,
                }
                for e in self.field.eliminations[-recent:][::-1]
            ],
        }

    def human_table_view(self) -> dict | None:
        """The live table the human sits at (None once they're out)."""
        ht = self.human_table
        return self._table_view(ht) if ht else None

    # ── advancing (the only things that move the world) ──────────────────────────

    def play_round(self, human_hand: HandFn) -> RoundReport:
        """Advance one round: the human plays one hand at their table; every other
        table plays 0/1/2 AI hands. Then settle eliminations and rebalance."""
        if self.is_complete():
            raise RuntimeError("tournament is already complete")
        if self.human_out:
            raise RuntimeError("human is out — use play_out() to finish the field")
        return self._round(human_hand)

    def play_out(self) -> list[RoundReport]:
        """Run the rest of the field AI-only to completion (after the human busts,
        or to fast-forward). Blinds keep rising on the round clock."""
        reports: list[RoundReport] = []
        while not self.is_complete() and self.rounds < self.config.max_rounds:
            reports.append(self._round())
        return reports

    def apply_live_round(self, human_result: dict[str, int]) -> RoundReport:
        """Fold a hand played LIVE at the human's table into the field, then pace
        the AI tables and settle.

        `human_result` is `{player_id: stack}` for every seat at the human's
        table after the live hand (the live game engine already conserved chips
        on that table). This is the entry point the Flask game-handler bridge
        calls at each hand boundary instead of `play_round` — the human's hand
        was driven by the real game, not a resolver callback."""
        if self.is_complete():
            raise RuntimeError("tournament is already complete")
        if self.human_out:
            raise RuntimeError("human is out — use play_out() to finish the field")
        return self._round(human_result=human_result)

    def _round(
        self, human_hand: HandFn | None = None, human_result: dict[str, int] | None = None
    ) -> RoundReport:
        level = self.current_level()
        pre = dict(self.field.stacks)
        table_of_player = {pid: t.table_id for t in self.seating.tables for pid in t.players}

        human_table_id = None
        if not self.human_out and (human_hand is not None or human_result is not None):
            ht = self.human_table
            human_table_id = ht.table_id if ht else None

        rng = random.Random(self.config.seed * 7_001 + self.rounds)
        for table in self.seating.tables:
            if human_table_id is not None and table.table_id == human_table_id:
                if human_result is not None:
                    self._apply_result(table, human_result)
                else:
                    self._play_hands(table, level, 1, human_hand)
            else:
                count = PACING_CHOICES[rng.randrange(len(PACING_CHOICES))]
                self._play_hands(table, level, count, self._ai_resolve)

        self.field.assert_conservation()

        busted = [
            (pid, pre[pid]) for pid in self.field.active_ids() if self.field.stacks[pid] <= 0
        ]
        gains_by_table: dict[int, dict[str, int]] = {}
        for pid, start in pre.items():
            tid = table_of_player.get(pid)
            if tid is None:
                continue
            gains_by_table.setdefault(tid, {})[pid] = self.field.stacks.get(pid, 0) - start
        eliminators = attribute_eliminators(busted, table_of_player, gains_by_table)
        events = self.field.record_eliminations(busted, self.rounds, eliminators)

        busted_ids = {pid for pid, _ in busted}
        for table in self.seating.tables:
            for pid in [p for p in table.players if p in busted_ids]:
                table.remove(pid)

        seat_moves = self.seating_manager.rebalance(self.seating)
        report = RoundReport(
            round_index=self.rounds,
            level=level,
            eliminations=tuple(events),
            seat_moves=tuple(seat_moves),
        )
        self.round_reports.append(report)
        self.rounds += 1
        return report

    def _apply_result(self, table, result: dict[str, int]) -> None:
        """Fold an externally-played hand result for one table into the field
        (used for the human's live table). Validates the resolver contract."""
        seat_order = table.players
        stacks = {pid: self.field.stacks[pid] for pid in seat_order}
        self._guard_table_result(stacks, result)
        for pid, new_stack in result.items():
            self.field.stacks[pid] = new_stack
        table.advance_button()

    def _play_hands(self, table, level: BlindLevel, num_hands: int, resolve: HandFn) -> None:
        """Play up to `num_hands` at one table. Stops early if the table drops
        below two players or a hand busts someone (so a hand is never built with a
        dead seat present — busted players are cleared at round end)."""
        for _ in range(num_hands):
            seat_order = table.players
            if len(seat_order) < 2:
                return
            if any(self.field.stacks.get(p, 0) <= 0 for p in seat_order):
                return
            self._hand_counter += 1
            seed = self.config.seed * 1_000_003 + self._hand_counter
            stacks = {p: self.field.stacks[p] for p in seat_order}
            result = resolve(
                seat_order=seat_order,
                stacks=stacks,
                level=level,
                button=table.dealer_index_in_occupied(),
                seed=seed,
            )
            self._guard_table_result(stacks, result)
            for pid, new_stack in result.items():
                self.field.stacks[pid] = new_stack
            table.advance_button()
            if any(self.field.stacks.get(p, 0) <= 0 for p in seat_order):
                return  # a bust ends the burst; the dead seat is cleared at round end

    @staticmethod
    def _guard_table_result(before: dict[str, int], after: dict[str, int]) -> None:
        if set(before) != set(after):
            raise AssertionError("hand resolver changed the set of players at the table")
        if sum(before.values()) != sum(after.values()):
            raise AssertionError(
                f"hand resolver did not conserve chips: in={sum(before.values())} "
                f"out={sum(after.values())}"
            )
