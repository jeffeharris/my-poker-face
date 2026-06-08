"""Direct in-memory 3-bet/4-bet attribution probe.

Mirrors simulate_bb100.run_6max_matchup (1 hero + 5 BaselineSolverBots) but
adds an on_decision hook that classifies each HERO preflop decision by scenario
(rfi / vs_open / vs_3bet) and tallies raise-or-all_in rate.

3-bet% = (raise|all_in at vs_open) / (all hero decisions at vs_open)
4-bet% = (raise|all_in at vs_3bet) / (all hero decisions at vs_3bet)
"""

import random
import sys
from collections import defaultdict

sys.path.insert(0, '/app')

from experiments._hand_loop import drive_hand
from experiments.simulate_bb100 import (
    ARCHETYPES,
    make_controller,
    make_game_state,
)
from poker.poker_state_machine import PokerStateMachine
from poker.strategy.preflop_classifier import classify_preflop_scenario
from poker.strategy.strategy_table import load_strategy_table

N_HANDS = 6000
BASE_SEED = 4242
BIG_BLIND = 100
STARTING_STACK = 10000

# (hero_archetype_key, label)
HEROES = [
    ('Baseline', 'BaselineSolverBot (distortion OFF)'),
    ('TAG', 'tag (distortion ON)'),
    ('LAG', 'lag (distortion ON)'),
    ('Maniac', 'maniac (distortion ON)'),
]

strategy_table = load_strategy_table()


def run_hero(archetype_key):
    """Run N_HANDS of 6-max (hero + 5 Baselines), tally hero preflop scenarios."""
    # tallies[scenario] -> [total_decisions, aggressive_decisions]
    tallies = defaultdict(lambda: [0, 0])

    hero_name = f'{archetype_key}_hero'
    opp_seats = [f'Base{i}' for i in range(5)]
    all_names = [hero_name] + opp_seats

    config_hero = ARCHETYPES[archetype_key]
    config_base = ARCHETYPES['Baseline']

    def on_decision(
        current_player, controller, action, raise_to, phase_name, gs, sim_current_street, decision
    ):
        if current_player.name != hero_name:
            return
        if phase_name == 'PRE_FLOP':
            scenario, _, _ = classify_preflop_scenario(gs)
            agg = 1 if action in ('raise', 'all_in') else 0
            tallies[scenario][0] += 1
            tallies[scenario][1] += agg
        else:
            # postflop AF tally: aggressive / call
            if action in ('raise', 'all_in', 'bet'):
                tallies['_pf_agg'][0] += 1
            elif action == 'call':
                tallies['_pf_call'][0] += 1

    for hand_num in range(N_HANDS):
        hand_seed = BASE_SEED + hand_num
        dealer_idx = hand_num % 6
        random.seed(hand_seed)

        gs = make_game_state(
            player_names=all_names,
            big_blind=BIG_BLIND,
            starting_stack=STARTING_STACK,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        sm.current_hand_seed = hand_seed

        controllers = [
            make_controller(hero_name, config_hero, strategy_table, sm, rng_seed=hand_seed)
        ]
        for i, seat in enumerate(opp_seats):
            controllers.append(
                make_controller(
                    seat, config_base, strategy_table, sm, rng_seed=hand_seed + 1_000_000 * (i + 1)
                )
            )

        hero_controller = controllers[0]
        drive_hand(
            sm,
            controllers,
            hero_name=hero_name,
            hero_controller=hero_controller,
            on_decision=on_decision,
        )

    return tallies


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


results = {}
for key, label in HEROES:
    t = run_hero(key)
    vs_open = t['vs_open']
    vs_3bet = t['vs_3bet']
    rfi = t['rfi']
    results[key] = (label, rfi, vs_open, vs_3bet)
    print(
        f"DONE {key}: vs_open n={vs_open[0]} agg={vs_open[1]} "
        f"({pct(vs_open[1], vs_open[0]):.1f}%) | "
        f"vs_3bet n={vs_3bet[0]} agg={vs_3bet[1]} "
        f"({pct(vs_3bet[1], vs_3bet[0]):.1f}%) | "
        f"AF={ (t['_pf_agg'][0]/max(t['_pf_call'][0],1)):.2f}",
        flush=True,
    )

print("\n==== 3-BET / 4-BET ATTRIBUTION (6-max, hero + 5 BaselineSolverBots) ====")
print(f"Hands per hero: {N_HANDS}  seed base: {BASE_SEED}\n")
hdr = f"{'hero':<34} {'3bet% (vs_open)':>20} {'4bet% (vs_3bet)':>20}"
print(hdr)
print("-" * len(hdr))
for key, label in HEROES:
    _, rfi, vs_open, vs_3bet = results[key]
    s3 = f"{pct(vs_open[1], vs_open[0]):.1f}%  (n={vs_open[0]})"
    s4 = f"{pct(vs_3bet[1], vs_3bet[0]):.1f}%  (n={vs_3bet[0]})"
    print(f"{label:<34} {s3:>20} {s4:>20}")

# Attribution deltas vs baseline
b_label, b_rfi, b_vsopen, b_vs3bet = results['Baseline']
base_3bet = pct(b_vsopen[1], b_vsopen[0])
base_4bet = pct(b_vs3bet[1], b_vs3bet[0])
print(f"\nBaseline chart-only 3bet={base_3bet:.1f}%  4bet={base_4bet:.1f}%")
for key in ('TAG', 'LAG', 'Maniac'):
    _, rfi, vs_open, vs_3bet = results[key]
    d3 = pct(vs_open[1], vs_open[0]) - base_3bet
    d4 = pct(vs_3bet[1], vs_3bet[0]) - base_4bet
    print(f"  {key:<8} distortion adds  3bet +{d3:.1f}pts   4bet +{d4:.1f}pts")
