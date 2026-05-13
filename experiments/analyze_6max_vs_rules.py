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
from poker.memory.opponent_model import OpponentModelManager
from experiments.simulate_bb100 import (
    ARCHETYPES, make_controller, make_game_state,
    DEFAULT_RULE_OPPONENTS, MAX_ACTIONS_PER_HAND, TERMINAL_PHASES,
    _make_seat_names, apply_adaptation_bias_override,
)
from typing import Optional


def run_hand_traced(sm, controllers, big_blind, archetype_seat,
                    opponent_manager: Optional[OpponentModelManager] = None,
                    hand_number: Optional[int] = None):
    """Drive one hand and capture per-action trace for one player.

    Returns dict with:
        actions:        list of (phase, action, raise_to) for archetype
        opp_actions:    list of (phase, name, action) for opponents
        pot_at_end:     final pot size before settlement
        final_stacks:   {name: stack}

    When ``opponent_manager`` is provided, each non-hero action is fed
    into the manager (observer=archetype_seat) so the hero controller's
    aggregated tendencies grow across hands. Default is no observation
    (behavior unchanged).
    """
    controller_map = {c.player_name: c for c in controllers}
    actions_arch: List[tuple] = []
    actions_opp: List[tuple] = []
    action_count = 0
    last_pot = 0

    # Phase 6.6/6.7a: reset sim-path aggressor state on hero's controller
    # at hand start. Production paths get this via MemoryManager.on_hand_start;
    # the sim bypasses MM, so we drive it directly here.
    hero_controller = controller_map.get(archetype_seat)
    if hero_controller is not None:
        hero_controller._sim_last_preflop_aggressor = None
        hero_controller._sim_recent_aggressor = None
    # Phase 6.7a: track current street so we can reset _sim_recent_aggressor
    # on each street transition (mirrors MemoryManager.on_action).
    sim_current_street: Optional[str] = None

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
            # Phase 6: feed non-hero action into hero's opponent model.
            if opponent_manager is not None:
                opponent_manager.observe_action(
                    observer=archetype_seat,
                    opponent=cur.name,
                    action=action,
                    phase=phase,
                    is_voluntary=True,
                    hand_number=hand_number,
                )

        new_gs = play_turn(gs, action, raise_to)

        # Phase 6.6: track the last accepted preflop aggressor on the
        # hero's controller for HU c-bet exploit gating. Matches the
        # production MemoryManager.on_action path — set only after
        # play_turn() has validated the action so we don't record
        # controller intent that the engine rejected.
        if (
            phase == 'PRE_FLOP'
            and action in ('raise', 'all_in')
            and hero_controller is not None
        ):
            hero_controller._sim_last_preflop_aggressor = cur.name

        # Phase 6.7a: per-street live aggressor for spot-aware exploit
        # selection. Reset on street change; update on accepted postflop
        # bet/raise/all_in.
        if hero_controller is not None:
            if sim_current_street != phase:
                hero_controller._sim_recent_aggressor = None
                sim_current_street = phase
            if (
                phase in ('FLOP', 'TURN', 'RIVER')
                and action in ('bet', 'raise', 'all_in')
            ):
                hero_controller._sim_recent_aggressor = cur.name
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


def analyze(archetype: str, n_hands: int, seed: int = 42,
            opponents: List[str] = None,
            adaptation_bias: Optional[float] = None,
            exploitation_strength: float = 1.0):
    strategy_table = load_strategy_table()
    big_blind = 100
    starting_stack = 10000

    if opponents is None:
        opponents = DEFAULT_RULE_OPPONENTS
    elif len(opponents) != 5:
        raise ValueError(f"opponents must have 5 entries, got {len(opponents)}")

    hero_name = archetype if archetype not in opponents else f"{archetype}_hero"
    opponent_seats = _make_seat_names(opponents)
    if hero_name in opponent_seats:
        hero_name = f"{archetype}_hero"
    archetype_seat = hero_name
    all_names = [archetype_seat] + opponent_seats

    config_arch = apply_adaptation_bias_override(
        ARCHETYPES[archetype], adaptation_bias
    )
    opp_configs = [ARCHETYPES[o] for o in opponents]

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

    # Phase 6: one manager across the whole analysis run so the hero's
    # observations of each opponent accumulate as hands play out.
    opponent_manager = OpponentModelManager()

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

        # Hero is at index 0; attach the shared manager so the controller
        # can read aggregated opponent tendencies during decide_action().
        controllers[0].opponent_model_manager = opponent_manager
        controllers[0].exploitation_strength = exploitation_strength

        # Record that each opponent was dealt this hand — necessary for
        # correct VPIP/PFR denominators (opponents who fold before action
        # reaches them never trigger observe_action, so hands_dealt has
        # to be incremented independently).
        opponent_manager.record_hand_dealt(
            observer=archetype_seat,
            opponents=opponent_seats,
            hand_number=hand_num,
        )

        trace = run_hand_traced(
            sm, controllers, big_blind, archetype_seat,
            opponent_manager=opponent_manager,
            hand_number=hand_num,
        )

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
        for opp_seat in opponent_seats:
            opp_delta = final.get(opp_seat, starting_stack) - starting_stack
            chip_transfer[opp_seat] += -opp_delta  # if opp lost (negative), this is positive

    # ── Print summary ────────────────────────────────────────────────────
    total_delta = sum(deltas)
    bb100 = (total_delta / big_blind) * 100 / n_hands
    bias_note = (
        f" [adaptation_bias overridden to {adaptation_bias}]"
        if adaptation_bias is not None else ""
    )
    print(f"\n{'=' * 70}")
    print(f"ANALYSIS: {archetype} vs {opponents}{bias_note}")
    print(f"{n_hands} hands @ {big_blind} BB, starting stack {starting_stack}, seed={seed}")
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

    # ── Phase 6 exploitation diagnostics ──
    # Counters live on the manager (persists across hands), not the
    # controller (recreated per hand).
    counters = getattr(opponent_manager, '_exploitation_counters', None)
    if counters:
        total = counters.get('decisions', 0)
        print("\n── EXPLOITATION DIAGNOSTICS ──")
        print(f"  total decisions:           {total}")
        if total > 0:
            for key in (
                'cold_start',
                'detected_hyper_aggressive',
                'detected_hyper_passive',
                'detected_tight_nit',
                'detected_high_fold_to_cbet',
                'fired',
                'fired_high_fold_to_cbet',
                'flop_as_preflop_aggressor_spots',
                'heads_up_cbet_spots',
                'spot_built_decisions',
                'selected_aggressor_decisions',
                'ambiguous_aggressor_decisions',
                'multiway_cbet_opportunity_logged',
                'detected_but_no_fire',
                'no_pattern_matched',
                'value_override_eligible_strong',
                'value_override_eligible_aggro',
                'value_override_fired',
            ):
                n = counters.get(key, 0)
                pct = 100 * n / total
                print(f"  {key:<32} {n:>6} ({pct:5.1f}%)")

    # Per-opponent tendencies (what the per-aggressor lookup sees).
    hero_models = opponent_manager.get_all_models_for_observer(archetype_seat)
    if hero_models:
        print("\n── PER-OPPONENT TENDENCIES (hero's view) ──")
        print(f"  {'opponent':<18} {'dealt':>6} {'acted':>6} "
              f"{'VPIP':>6} {'PFR':>6} {'AF':>6} {'all_in%':>8} "
              f"{'f2cbet':>7} {'cbet_n':>7} triggers")
        for opp_name, model in sorted(hero_models.items()):
            t = model.tendencies
            triggers = []
            if t.aggression_factor > 5.0:
                triggers.append('AF>5')
            if t.all_in_frequency > 0.30:
                triggers.append('AI>30%')
            if t.vpip > 0.60 and t.aggression_factor < 0.80:
                triggers.append('PASSIVE')
            if t.vpip < 0.15:
                triggers.append('NIT')
            # Phase 6.6: high_fold_to_cbet detection trigger
            if t.fold_to_cbet > 0.60 and t._cbet_faced_count >= 5:
                triggers.append('F2C>0.60')
            trigger_str = ' '.join(triggers) if triggers else '-'
            print(f"  {opp_name:<18} "
                  f"{t.hands_dealt:>6} {t.hands_observed:>6} "
                  f"{t.vpip:>6.2f} {t.pfr:>6.2f} "
                  f"{t.aggression_factor:>6.2f} {t.all_in_frequency:>8.2f} "
                  f"{t.fold_to_cbet:>7.2f} {t._cbet_faced_count:>7d} "
                  f"{trigger_str}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('archetype', type=str, help='ARCHETYPES key to analyze (e.g. Maniac, LAG, Nit)')
    p.add_argument('--hands', type=int, default=200)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument(
        '--opponents', type=str, default=None,
        help='Comma-separated list of exactly 5 ARCHETYPES keys '
             '(e.g. "CaseBot,CaseBot,CaseBot,GTO-Lite,ABCBot"). '
             'Duplicates allowed; seats get suffixed (CaseBot01, etc.).',
    )
    p.add_argument(
        '--adaptation-bias', type=float, default=None,
        help='Override adaptation_bias on the hero archetype anchors. '
             'Phase 6 validation gates: 0.05 = no-exploit floor, '
             '0.85 = full exploitation.',
    )
    p.add_argument(
        '--exploitation-strength', type=float, default=1.0,
        help='Global multiplier on exploitation offset magnitudes. '
             'Used for the calibration sweep — default 1.0; sweep '
             '[1.0, 1.5, 2.0, 2.5] to find optimal magnitude.',
    )
    args = p.parse_args()

    if args.archetype not in ARCHETYPES:
        print(f"Unknown archetype: {args.archetype}")
        print(f"Available: {[k for k, v in ARCHETYPES.items() if v.get('kind') != 'rule_bot']}")
        sys.exit(1)

    opponents = None
    if args.opponents:
        opponents = [o.strip() for o in args.opponents.split(',')]
        for o in opponents:
            if o not in ARCHETYPES:
                print(f"Unknown opponent: {o}")
                sys.exit(1)

    analyze(
        args.archetype, args.hands, args.seed,
        opponents=opponents,
        adaptation_bias=args.adaptation_bias,
        exploitation_strength=args.exploitation_strength,
    )


if __name__ == '__main__':
    main()
