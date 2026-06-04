"""`HandResolver` backed by the real poker engine, with no-LLM bots.

This is the only module in the package that depends on the poker engine and the
experiment harness. It reuses the exact per-hand pattern proven in
`experiments/simulate_bb100.py` / `experiments/sng_runner.py`: build a fresh
`PokerGameState` with per-seat stacks, drive one hand with
`champion_challenger.run_cc_hand`, and read back the resulting stacks. Every
controller is a tiered solver bot or a rule bot — zero LLM calls.

Kept out of `tournament/__init__.py` on purpose so `import tournament` stays
engine-free for the pure unit tests.
"""

import random

from experiments.champion_challenger import run_cc_hand
from experiments.simulate_bb100 import ARCHETYPES, make_controller
from poker.poker_game import Player, PokerGameState, create_deck
from poker.poker_state_machine import PokerStateMachine
from poker.strategy.strategy_table import load_strategy_table
from poker.table.seat import PersonaSeat

from .blinds import BlindLevel

# Non-escalating blind config: the director controls the blind level by setting
# `current_ante` per hand, so the engine's own per-hand-count escalation must
# never fire (we rebuild the table fresh each hand and never advance past
# HAND_OVER, so it wouldn't anyway — this is belt-and-suspenders).
_NO_ESCALATION = {'growth': 1.0, 'hands_per_level': 10**9, 'max_blind': 0}


class EngineHandResolver:
    """Plays one real hand at a table using tiered/rule controllers."""

    def __init__(self, entries: dict[str, str], strategy_table=None):
        """`entries` maps player_id -> archetype name (a key in ARCHETYPES)."""
        unknown = [a for a in set(entries.values()) if a not in ARCHETYPES]
        if unknown:
            raise ValueError(f"unknown archetype(s): {sorted(unknown)}")
        self.entries = dict(entries)
        self.strategy_table = strategy_table or load_strategy_table()

    def resolve(
        self,
        seat_order: list[str],
        stacks: dict[str, int],
        level: BlindLevel,
        button: int,
        seed: int,
    ) -> dict[str, int]:
        big_blind = level.big_blind
        players = tuple(
            Player(
                name=pid,
                stack=stacks[pid],
                is_human=False,
                personality_id=pid,
                seat_id=PersonaSeat(pid),
            )
            for pid in seat_order
        )
        gs = PokerGameState(
            players=players,
            deck=create_deck(shuffled=True, random_seed=seed),
            current_ante=big_blind,
            last_raise_amount=big_blind,
            current_dealer_idx=button % len(players),
        )
        sm = PokerStateMachine(gs, blind_config=_NO_ESCALATION, record_snapshots=False)
        # Tell the SM the deck seed is provided so it doesn't reshuffle from
        # global random on the first initialize-hand transition.
        sm.current_hand_seed = seed

        controllers = [
            make_controller(
                pid,
                ARCHETYPES[self.entries[pid]],
                self.strategy_table,
                sm,
                rng_seed=seed + 1_000_000 * i,
            )
            for i, pid in enumerate(seat_order)
        ]

        # Several rule-bot fallbacks read the global RNG; seed it for determinism
        # (mirrors the sim runners).
        random.seed(seed)
        run_cc_hand(sm, controllers, big_blind, hand_number=0)

        return {p.name: p.stack for p in sm.game_state.players}
