#!/usr/bin/env python3
"""Diagnostic: WHY does Maniac (or LAG) crush a 6-max rule_bot mix?

Runs a smaller sample (default 200 hands) of one archetype vs the standard
rule_bot mix, but instruments run_hand to record per-action and per-hand
details. Outputs:
  - Action distribution (VPIP, PFR, AF, fold-to-bet rate)
  - Average pot size when this archetype wins vs loses
  - Per-opponent chip transfer (who's losing to whom)
  - Hand outcome split (preflop steal vs flop+ vs showdown)

Usage:
    docker compose exec backend python -m experiments.analyze_6max_vs_rules Maniac
    docker compose exec backend python -m experiments.analyze_6max_vs_rules LAG --hands 500
"""
import argparse
import sys
import os
import random
from collections import Counter, defaultdict
from typing import Dict, List
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poker.poker_game import (
    PokerGameState, Player, create_deck,
    play_turn, advance_to_next_active_player,
)
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.strategy.strategy_table import load_strategy_table
from experiments.simulate_bb100 import (
    ARCHETYPES, make_controller, make_game_state,
    DEFAULT_RULE_OPPONENTS, MAX_ACTIONS_PER_HAND, TERMINAL_PHASES,
)


def run_hand_traced(sm, controllers, big_blind, archetype_seat):
    """Drive one hand and capture per-action trace for one player.

    Returns dict with:
        actions:        list of (phase, action, raise_to) for archetype
        opp_actions:    list of (phase, name, action) for opponents
        pot_at_end:     final pot size before settlement
        final_stacks:   {name: stack}
    """
    controller_map = {c.player_name: c for c in controllers}
    actions_arch: List[tuple] = []
    actions_opp: List[tuple] = []
    action_count = 0
    last_pot = 0

    while sm.phase not in TERMINAL_PHASES:
        sm.run_until(list(TERMINAL_PHASES))
        if sm.phase in TERMINAL_PHASES:
            break
        gs = sm.game_state
        last_pot = gs.pot.get('total', 0) if isinstance(gs.pot, dict) else 0

        if gs.run_it_out:
            sm.game_state = gs.update(run_it_out=False, awaiting_action=False)
            next_phase = {
                PokerPhase.PRE_FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.TURN: PokerPhase.DEALING_CARDS,
                PokerPhase.RIVER: PokerPhase.EVALUATING_HAND,
            }.get(sm.phase, PokerPhase.EVALUATING_HAND)
            sm.phase = next_phase
            continue

        cur = gs.current_player
        controller = controller_map[cur.name]
        controller.state_machine = sm
        decision = controller.decide_action()
        action = decision['action']
        raise_to = decision.get('raise_to', 0) or 0
        phase = sm.phase.name

        if cur.name == archetype_seat:
            actions_arch.append((phase, action, raise_to))
        else:
            actions_opp.append((phase, cur.name, action))

        new_gs = play_turn(gs, action, raise_to)
        advanced = advance_to_next_active_player(new_gs)
        sm.game_state = advanced if advanced is not None else new_gs

        action_count += 1
        if action_count >= MAX_ACTIONS_PER_HAND:
            break

    final_stacks = {p.name: p.stack for p in sm.game_state.players}
    return {
        'actions': actions_arch,
        'opp_actions': actions_opp,
        'pot_at_end': last_pot,
        'final_stacks': final_stacks,
    }


def analyze(archetype: str, n_hands: int, seed: int = 42):
    strategy_table = load_strategy_table()
    big_blind = 100
    starting_stack = 10000

    archetype_seat = 'P1'
    opponent_seats = ['P2', 'P3', 'P4', 'P5', 'P6']
    all_names = [archetype_seat] + opponent_seats

    config_arch = ARCHETYPES[archetype]
    opp_configs = [ARCHETYPES[o] for o in DEFAULT_RULE_OPPONENTS]

    # Per-archetype stats
    total_actions_pf = 0
    total_actions_post = 0
    folds_pf = 0
    raises_pf = 0
    calls_pf = 0
    aggressive_post = 0       # bets/raises postflop
    passive_post = 0          # checks/calls postflop
    folds_post = 0

    # Per-hand outcomes
    deltas: List[float] = []
    wins = 0
    losses = 0
    preflop_only_hands = 0    # archetype acted only preflop (folded or took it down preflop)
    showdown_hands = 0        # got to river action

    # Chip transfer per opponent name
    chip_transfer: Dict[str, int] = defaultdict(int)

    for hand_num in range(n_hands):
        hand_seed = seed + hand_num
        dealer_idx = hand_num % 6

        gs = make_game_state(
            player_names=all_names, big_blind=big_blind,
            starting_stack=starting_stack, dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)

        controllers = [
            make_controller(archetype_seat, config_arch, strategy_table, sm,
                            rng_seed=hand_seed)
        ]
        for i, (seat, cfg) in enumerate(zip(opponent_seats, opp_configs)):
            controllers.append(
                make_controller(seat, cfg, strategy_table, sm,
                                rng_seed=hand_seed + 1_000_000 * (i + 1))
            )

        trace = run_hand_traced(sm, controllers, big_blind, archetype_seat)

        # Action stats
        had_post = False
        for phase, action, _ in trace['actions']:
            if phase == 'PRE_FLOP':
                total_actions_pf += 1
                if action == 'fold': folds_pf += 1
                elif action == 'raise': raises_pf += 1
                elif action == 'call': calls_pf += 1
            else:
                had_post = True
                total_actions_post += 1
                if action == 'fold':
                    folds_post += 1
                elif action in ('raise', 'all_in'):
                    aggressive_post += 1
                else:
                    passive_post += 1

        # Hand outcome
        final = trace['final_stacks']
        delta = final.get(archetype_seat, starting_stack) - starting_stack
        deltas.append(delta)
        if delta > 0: wins += 1
        elif delta < 0: losses += 1

        if not had_post:
            preflop_only_hands += 1
        # Track showdown hands (river action by archetype)
        if any(p == 'RIVER' for p, _, _ in trace['actions']):
            showdown_hands += 1

        # Chip transfer: sum opponent deltas (positive = opponent lost = chips flowed to archetype's pile, indirectly)
        # NOTE: this isn't direct 1:1 chip transfer (multiway pots split). It's correlation.
        for opp_seat, opp_name in zip(opponent_seats, DEFAULT_RULE_OPPONENTS):
            opp_delta = final.get(opp_seat, starting_stack) - starting_stack
            chip_transfer[opp_name] += -opp_delta  # if opp lost (negative), this is positive
        # Subtract the archetype's gain to get net "they lost to me" approximation
        # (loose since multiway)

    # ── Print summary ────────────────────────────────────────────────────
    total_delta = sum(deltas)
    bb100 = (total_delta / big_blind) * 100 / n_hands
    print(f"\n{'=' * 70}")
    print(f"ANALYSIS: {archetype} vs {DEFAULT_RULE_OPPONENTS}")
    print(f"{n_hands} hands @ {big_blind} BB, starting stack {starting_stack}")
    print(f"{'=' * 70}\n")
    print(f"Net result: {total_delta:+d} chips total, {bb100:+.1f} bb/100\n")

    print(f"Hands won:   {wins:>4} ({100*wins/n_hands:.0f}%)")
    print(f"Hands lost:  {losses:>4} ({100*losses/n_hands:.0f}%)")
    print(f"Hands push:  {n_hands - wins - losses:>4}\n")

    print(f"Preflop-only hands: {preflop_only_hands:>4} ({100*preflop_only_hands/n_hands:.0f}%)")
    print(f"Reached river:      {showdown_hands:>4} ({100*showdown_hands/n_hands:.0f}%)\n")

    print("── PREFLOP ACTIONS ──")
    print(f"  Total:   {total_actions_pf}")
    print(f"  Folds:   {folds_pf:>5} ({100*folds_pf/max(1,total_actions_pf):.0f}%)")
    print(f"  Raises:  {raises_pf:>5} ({100*raises_pf/max(1,total_actions_pf):.0f}%)")
    print(f"  Calls:   {calls_pf:>5} ({100*calls_pf/max(1,total_actions_pf):.0f}%)")
    vpip_actions = raises_pf + calls_pf
    print(f"  VPIP:    {100*vpip_actions/max(1,total_actions_pf):.0f}%")
    print(f"  PFR:     {100*raises_pf/max(1,total_actions_pf):.0f}%")

    print("\n── POSTFLOP ACTIONS ──")
    print(f"  Total:        {total_actions_post}")
    print(f"  Folds:        {folds_post:>5} ({100*folds_post/max(1,total_actions_post):.0f}%)")
    print(f"  Aggressive:   {aggressive_post:>5} ({100*aggressive_post/max(1,total_actions_post):.0f}%)")
    print(f"  Passive:      {passive_post:>5} ({100*passive_post/max(1,total_actions_post):.0f}%)")
    af = aggressive_post / max(1, passive_post)
    print(f"  AggFactor:    {af:.2f}")

    print("\n── CHIP TRANSFER FROM OPPONENTS (sum of opponent losses) ──")
    print("  (positive = opponent net-lost chips this session)")
    for opp_name in sorted(chip_transfer.keys(), key=lambda n: -chip_transfer[n]):
        net = chip_transfer[opp_name]
        print(f"  {opp_name:<18} {net:>+8d} chips ({net/big_blind:>+.0f} BB)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('archetype', type=str, help='ARCHETYPES key to analyze (e.g. Maniac, LAG, Nit)')
    p.add_argument('--hands', type=int, default=200)
    p.add_argument('--seed', type=int, default=42)
    args = p.parse_args()

    if args.archetype not in ARCHETYPES:
        print(f"Unknown archetype: {args.archetype}")
        print(f"Available: {[k for k, v in ARCHETYPES.items() if v.get('kind') != 'rule_bot']}")
        sys.exit(1)

    analyze(args.archetype, args.hands, args.seed)


if __name__ == '__main__':
    main()
