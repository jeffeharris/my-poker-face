"""Full-stat archetype validation probe (backlog #1).

Mirrors simulate_bb100.run_6max_matchup (1 hero + 5 BaselineSolverBots) but
adds an on_decision hook that tallies, per HERO, every behavioral stat that
ARCHETYPE_TARGETS bands — then scores realized behavior against the band.

Stats + denominators (match poker/archetype_targets.py + the review route):
  vpip         = hands hero voluntarily put money in preflop / hands
  pfr          = hands hero raised|all_in preflop / hands
  threebet     = raise|all_in AT a vs_open node / decisions facing an open
  fourbet      = raise|all_in AT a vs_3bet node / decisions facing a 3-bet
  fold_to_3bet = fold AT a vs_3bet node / decisions facing a 3-bet
  af           = postflop (bet+raise+all_in) / call  (a ratio)
  all_in       = hands with any all_in action / hands

Caveat (banked in the handoff): the field is all-Baseline (tight), so absolute
AF is compressed and 3-bet rates run a touch low vs a looser live field. It is
still a clean, deterministic instrument (same field for every hero).
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
from poker.archetype_targets import ARCHETYPE_TARGETS, STAT_LABELS, score_stat
from poker.poker_state_machine import PokerStateMachine
from poker.strategy.preflop_classifier import classify_preflop_scenario
from poker.strategy.strategy_table import load_strategy_table

N_HANDS = 6000
BASE_SEED = 4242
BIG_BLIND = 100
STARTING_STACK = 10000

# (hero_archetype_key, target_archetype_key, label)
# target_archetype_key indexes ARCHETYPE_TARGETS; None = no band (control).
HEROES = [
    ('Baseline', None, 'BaselineSolverBot (distortion OFF)'),
    ('Nit', 'nit', 'nit'),
    ('Rock', 'rock', 'rock'),
    ('TAG', 'tag', 'tag'),
    ('LAG', 'lag', 'lag'),
    ('Maniac', 'maniac', 'maniac'),
    ('Calling Station', 'calling_station', 'calling_station'),
    ('WeakFish', 'weak_fish', 'weak_fish'),
]

# Stat display order.
STAT_ORDER = ['vpip', 'pfr', 'threebet', 'fourbet', 'fold_to_3bet', 'af', 'all_in']

strategy_table = load_strategy_table()


def run_hero(archetype_key):
    """Run N_HANDS of 6-max (hero + 5 Baselines), return a stats dict."""
    # Node-level tallies: [total_decisions, aggressive_decisions, fold_decisions]
    nodes = defaultdict(lambda: [0, 0, 0])
    # Postflop AF parts.
    pf = {'agg': 0, 'call': 0}
    # Per-hand accumulators (denominator = hands_dealt).
    counts = {'hands': 0, 'vpip': 0, 'pfr': 0, 'all_in': 0}
    # Reset per hand inside the loop.
    flags = {'vpip': False, 'pfr': False, 'all_in': False}

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
        if action == 'all_in':
            flags['all_in'] = True
        if phase_name == 'PRE_FLOP':
            if action in ('call', 'raise', 'bet', 'all_in'):
                flags['vpip'] = True
            if action in ('raise', 'all_in'):
                flags['pfr'] = True
            scenario, _, _ = classify_preflop_scenario(gs)
            nodes[scenario][0] += 1
            if action in ('raise', 'all_in'):
                nodes[scenario][1] += 1
            if action == 'fold':
                nodes[scenario][2] += 1
        else:
            if action in ('raise', 'all_in', 'bet'):
                pf['agg'] += 1
            elif action == 'call':
                pf['call'] += 1

    for hand_num in range(N_HANDS):
        hand_seed = BASE_SEED + hand_num
        dealer_idx = hand_num % 6
        random.seed(hand_seed)

        flags['vpip'] = flags['pfr'] = flags['all_in'] = False

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

        counts['hands'] += 1
        counts['vpip'] += 1 if flags['vpip'] else 0
        counts['pfr'] += 1 if flags['pfr'] else 0
        counts['all_in'] += 1 if flags['all_in'] else 0

    vs_open = nodes['vs_open']
    vs_3bet = nodes['vs_3bet']
    hands = max(counts['hands'], 1)
    return {
        # (value, sample_size) per stat
        'vpip': (pct(counts['vpip'], hands), counts['hands']),
        'pfr': (pct(counts['pfr'], hands), counts['hands']),
        'threebet': (pct(vs_open[1], vs_open[0]), vs_open[0]),
        'fourbet': (pct(vs_3bet[1], vs_3bet[0]), vs_3bet[0]),
        'fold_to_3bet': (pct(vs_3bet[2], vs_3bet[0]), vs_3bet[0]),
        'af': (pf['agg'] / max(pf['call'], 1), pf['agg'] + pf['call']),
        'all_in': (pct(counts['all_in'], hands), counts['hands']),
    }


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


MARK = {'pass': 'ok ', 'warn': 'WARN', 'fail': 'FAIL', 'low_n': 'low-n', 'no_data': '--'}


def fmt(stat, value):
    return f'{value:.2f}' if stat == 'af' else f'{value:.1f}'


results = {}
for key, target_key, label in HEROES:
    stats = run_hero(key)
    results[key] = (target_key, label, stats)
    summary = '  '.join(f'{s}={fmt(s, stats[s][0])}' for s in STAT_ORDER)
    print(f'DONE {label:<18} {summary}', flush=True)

print('\n==== ARCHETYPE VALIDATION (6-max, hero + 5 BaselineSolverBots) ====')
print(f'Hands per hero: {N_HANDS}  seed base: {BASE_SEED}')
print('Field is all-Baseline (tight): AF compressed, 3-bet a touch low vs live.\n')

hdr = f"{'archetype':<16} {'stat':<14} {'actual':>8} {'target band':>14}  result"
print(hdr)
print('-' * len(hdr))
fails = []
for key, target_key, label in HEROES:
    _, _, stats = results[key]
    band_table = ARCHETYPE_TARGETS.get(target_key) if target_key else None
    for stat in STAT_ORDER:
        value, sample = stats[stat]
        if band_table is None:
            print(f'{label:<16} {STAT_LABELS[stat]:<14} {fmt(stat, value):>8} {"(control)":>14}')
            continue
        lo, hi = band_table[stat]
        verdict = score_stat(value, (lo, hi), sample)
        band_s = f'{fmt(stat, lo)}-{fmt(stat, hi)}'
        print(
            f'{target_key:<16} {STAT_LABELS[stat]:<14} {fmt(stat, value):>8} '
            f'{band_s:>14}  {MARK[verdict]}  (n={sample})'
        )
        if verdict == 'fail':
            fails.append((target_key, stat, value, lo, hi))
    print()

if fails:
    print('==== OUT OF BAND (fail) ====')
    for arch, stat, value, lo, hi in fails:
        direction = 'HIGH' if value > hi else 'LOW'
        print(
            f'  {arch:<16} {stat:<13} {fmt(stat, value)} {direction} (band {fmt(stat, lo)}-{fmt(stat, hi)})'
        )
else:
    print('All scored stats within band or warn. No hard fails.')
