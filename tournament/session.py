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
from .director import FakeHandResolver, RoundReport, build_initial_state
from .field import TournamentField, attribute_eliminators
from .seating import Seating, SeatingManager

# A jittered hand count per AI table per human hand. Weighted so the mean is
# exactly 1.0 (0+1+1+2)/4 — the field tracks the human without drifting.
PACING_CHOICES = (0, 1, 1, 2)

# Fraction of the field that finishes "in the money" — a DISPLAY-ONLY cutoff
# (no payouts exist yet) so the standings can show ITM/OTM + the bubble. Real
# MTTs pay ~10–15%; the P2 economy design front-loads the top ~30%. Replaced by
# the actual payout structure when the economy ships. Tune freely.
IN_THE_MONEY_FRACTION = 0.15


def paid_places_for(field_size: int) -> int:
    """How many places are 'in the money' for a given field size (min 2)."""
    return max(2, round(field_size * IN_THE_MONEY_FRACTION))


# Signature shared by the AI resolver's `.resolve` and the human-table callback.
HandFn = Callable[..., dict[str, int]]


class TournamentSession:
    """A multi-table tournament with one live human at one table."""

    def __init__(
        self,
        config: TournamentConfig,
        ai_resolver,
        human_id: str | None = None,
        *,
        entries: dict[str, str] | None = None,
    ):
        self.config = config
        self._ai_resolve: HandFn = ai_resolver.resolve
        self.schedule = config.blind_schedule()
        self.seating_manager = SeatingManager()

        player_ids, self.entries, self.field, self.seating = build_initial_state(
            config, entries=entries
        )
        self.human_id = human_id or player_ids[0]
        if self.human_id not in self.entries:
            raise ValueError(f"human_id {self.human_id!r} is not in the field")

        self.rounds = 0  # blind clock + seed source; one per round
        self._hand_counter = 0  # unique per hand played (seed source)
        self.round_reports: list[RoundReport] = []
        self.field.assert_conservation()

    @classmethod
    def for_single_table(
        cls,
        *,
        entries: dict[str, str],
        human_id: str,
        starting_stack: int,
        seed: int = 0,
    ) -> 'TournamentSession':
        """A one-table tournament from REAL players — the unification of the
        legacy single-table game. `entries` is an ordered `name -> archetype`
        map (the human + their opponents, in seat order); `human_id` is the
        human's name.

        The session is a passive field/standings/completion tracker here: the
        live poker state machine remains the authority for play AND blinds (it
        self-escalates from its own `blind_config`), so the blind schedule below
        is unused — only `current_level()` would read it, and a single-table game
        never shows the standings clock. No AI resolver runs (there are no other
        tables), so a no-op `FakeHandResolver` is fine."""
        n = len(entries)
        config = TournamentConfig(
            field_size=n,
            table_size=n,
            starting_stack=starting_stack,
            seed=seed,
        )
        return cls(config, FakeHandResolver(), human_id=human_id, entries=entries)

    def fold_live_hand(self, stacks_after: dict[str, int], eliminator: str | None = None) -> list:
        """Fold a completed live hand at the (single) table into the field and
        record any eliminations — without touching seating, blinds, or pacing.

        This is the single-table analog of `apply_live_round`: the live poker
        engine owns the table (players, busts, blinds); the session only needs
        the resulting stacks to keep the field standings + elimination log (and
        thus completion) correct. `stacks_after` maps each still-active player to
        their post-hand stack; `eliminator` is the hand's winner (best-effort
        attribution for anyone who busted). Returns the Elimination events."""
        active = self.field.active_ids()
        pre = {pid: self.field.stacks[pid] for pid in active}
        for pid, stack in stacks_after.items():
            if pid in self.field.stacks:
                self.field.stacks[pid] = stack
        busted = [(pid, pre[pid]) for pid in active if self.field.stacks[pid] <= 0]
        attribution = {pid: eliminator for pid, _ in busted if eliminator}
        events = self.field.record_eliminations(busted, self.rounds, attribution)
        self._hand_counter += 1
        self.rounds += 1
        self.field.assert_conservation()
        return events

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

    def leaderboard(self, top: int = 5) -> list[dict]:
        """The current chip leaders (1 = chip leader), highest stack first."""
        ranked = sorted(self.field.stacks.items(), key=lambda kv: -kv[1])
        return [
            {
                'rank': i + 1,
                'player_id': pid,
                'stack': stack,
                'is_human': pid == self.human_id,
            }
            for i, (pid, stack) in enumerate(ranked[:top])
        ]

    def payout_view(self) -> dict:
        """In-the-money status (display-only until real payouts land): how many
        places pay, how many busts until the bubble bursts, and whether the
        remaining field has all locked up a cash."""
        paid = paid_places_for(self.field.field_size)
        remaining = self.field.active_count
        return {
            'paid_places': paid,
            'players_to_money': max(0, remaining - paid),
            'on_bubble': remaining == paid + 1,
            'in_money': remaining <= paid,  # everyone left has cashed
        }

    def next_level_view(self) -> dict | None:
        """The blind level after the current one, with how many of the human's
        hands until it hits (time is player-gated, so the clock is in hands, not
        minutes). None once the schedule is at its top level."""
        sched = self.schedule
        cur_idx = self.rounds // sched.rounds_per_level
        if cur_idx + 1 >= len(sched.levels):
            return None
        nxt = sched.levels[cur_idx + 1]
        return {
            'level': nxt.level,
            'small_blind': nxt.small_blind,
            'big_blind': nxt.big_blind,
            'ante': nxt.ante,
            'hands_until': max(0, (cur_idx + 1) * sched.rounds_per_level - self.rounds),
        }

    def _human_in_money(self, paid_places: int) -> bool:
        """Has the human secured a cash? Out → their finish paid; in → the field
        has collapsed to the paid places (all survivors are ITM)."""
        if self.human_out:
            rank = self.human_rank()
            return rank is not None and rank <= paid_places
        return self.field.active_count <= paid_places

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
        payout = self.payout_view()
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
            'next_level': self.next_level_view(),
            'leaders': self.leaderboard(),
            'payout': payout,
            'human': {
                'player_id': self.human_id,
                'out': self.human_out,
                'rank': self.human_rank(),
                'stack': self.field.stacks.get(self.human_id),
                'table_id': ht.table_id if ht else None,
                'in_money': self._human_in_money(payout['paid_places']),
            },
            'tables': [
                self._table_view(t) for t in sorted(self.seating.tables, key=lambda t: t.table_id)
            ],
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

    def advance_round(self) -> RoundReport | None:
        """Advance the field exactly ONE round, AI-only (no human seam).

        For an autonomous tournament (no live human driving it): the world ticker
        calls this once per tick so a Main Event plays out *at world pace*,
        incrementally, instead of resolving in a single `play_out()` burst — the
        same way the cash tables advance a step per tick. Returns the round's
        report, or None if the field is already complete (or hit `max_rounds`).

        Identical to one iteration of `play_out`'s loop; `_round()` with no human
        args advances every table (including the nominal human seat) via the AI
        resolver, so it's correct for a field with no real human participant."""
        if self.is_complete() or self.rounds >= self.config.max_rounds:
            return None
        return self._round()

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
        tables_before = {t.table_id for t in self.seating.tables}

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

        busted = [(pid, pre[pid]) for pid in self.field.active_ids() if self.field.stacks[pid] <= 0]
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
        tables_after = {t.table_id for t in self.seating.tables}
        report = RoundReport(
            round_index=self.rounds,
            level=level,
            eliminations=tuple(events),
            seat_moves=tuple(seat_moves),
            broken_tables=tuple(sorted(tables_before - tables_after)),
        )
        self.round_reports.append(report)
        self.rounds += 1
        return report

    def _apply_result(self, table, result: dict[str, int]) -> None:
        """Fold an externally-played hand result for one table into the field
        (used for the human's live table). Validates the resolver contract.

        A seat present in `table.players` but absent from `field.stacks` is a
        desync (a player the live game still seats but this session already
        busted — possible if the live game and session cold-load from slightly
        different save points after a mid-hand restart/eviction). `.get(pid, 0)`
        treats such a seat as out (stack 0) so the hand boundary does NOT KeyError
        and PERMANENTLY freeze the human's live game; the guard then reconciles
        against the live result. The deeper fix is atomic game+session persistence
        + a cold-load reconcile (see the live-table hardening follow-up)."""
        seat_order = table.players
        stacks = {pid: self.field.stacks.get(pid, 0) for pid in seat_order}
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

    # ── serialization (for tournament persistence) ──────────────────────────────

    def to_dict(self) -> dict:
        """Serialize the meta-layer state. The resolver, schedule, seating
        manager and ephemeral round_reports are NOT stored — they are rebuilt
        (the resolver is passed back in on `from_dict`, the rest are pure
        functions of the config/field)."""
        return {
            'config': self.config.to_dict(),
            'human_id': self.human_id,
            'rounds': self.rounds,
            'hand_counter': self._hand_counter,
            'field': self.field.to_dict(),
            'seating': self.seating.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict, ai_resolver) -> 'TournamentSession':
        """Rebuild a session from `to_dict` output plus a freshly built resolver
        (resolvers aren't serialized — the caller reconstructs one from the
        stored `resolver_kind`). Asserts chip conservation, so a corrupt or
        partial restore fails loudly instead of silently dropping chips."""
        config = TournamentConfig.from_dict(d['config'])
        # __init__ rebuilds genesis state and validates human_id against the
        # field — so it MUST be seeded with the SAVED entries, not left to
        # regenerate a synthetic P## field. Without this, a real-persona field
        # (every P3 invite/autonomous tournament, whose human_id is `human:<owner>`
        # or a real persona id) fails to rehydrate on cold load with
        # "human_id ... is not in the field". We then overwrite the mutable world
        # with the fully-restored field/seating/counters.
        session = cls(config, ai_resolver, human_id=d['human_id'], entries=d['field']['entries'])
        session.field = TournamentField.from_dict(d['field'])
        session.seating = Seating.from_dict(d['seating'])
        session.entries = dict(session.field.entries)
        session.rounds = d['rounds']
        session._hand_counter = d['hand_counter']
        session.field.assert_conservation()
        return session
