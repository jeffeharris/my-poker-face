"""Mixed-field archetype validation probe (backlog #1, absolute instrument).

The controlled probe (`archetype_3bet_probe.py`) seats 1 hero + 5 tight
BaselineSolverBots. That all-Baseline field is great as a deterministic A/B
(same field both arms) but biases ABSOLUTE stats vs the target bands: tight
openers 3-bet a strong range, so heroes correctly fold-to-3bet far more than
they would vs a live/looser field, and 3-bet rates skew.

This probe instead seats the 7 production archetypes AT THE SAME TABLE and
measures every one of them simultaneously — the realistic mixed field the
ARCHETYPE_TARGETS bands are calibrated for (and what the review tool's
`source=sim` reading of `archetype_stat_counts` captures from the cash sim).

7 archetypes, 6 seats: one archetype sits out each hand (rotated by hand_num),
so every archetype is measured against a realistic rotating mix of the others.

Stats + denominators match poker/archetype_targets.py exactly.
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

N_HANDS = 9000
BASE_SEED = 4242
BIG_BLIND = 100
STARTING_STACK = 10000

# (sim_archetype_key, target_archetype_key). The 7 production archetypes.
FIELD = [
    ('Nit', 'nit'),
    ('Rock', 'rock'),
    ('TAG', 'tag'),
    ('LAG', 'lag'),
    ('Maniac', 'maniac'),
    ('Calling Station', 'calling_station'),
    ('WeakFish', 'weak_fish'),
]

STAT_ORDER = ['vpip', 'pfr', 'threebet', 'fourbet', 'fold_to_3bet', 'af', 'all_in']

strategy_table = load_strategy_table()


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


# Per-archetype accumulators.
nodes = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # arch -> scenario -> [tot,agg,fold]
pf = defaultdict(lambda: {'agg': 0, 'call': 0})
counts = defaultdict(lambda: {'hands': 0, 'vpip': 0, 'pfr': 0, 'all_in': 0})

# name -> target_archetype_key for the seats in the current hand.
name_to_arch = {}
# Per-hand scratch: name -> flags.
hand_flags = {}
# Per-hand: '_opener' -> name of the hand's RFI opener (first preflop raiser).
# fourbet / fold_to_3bet are conditioned on the actor being the RFI opener — the
# `vs_3bet` node (raise count == 2) otherwise sweeps in SQUEEZE defence (you
# cold-called an open, then someone 3-bet), which is a different stat and folds
# ~100%, contaminating fold_to_3bet badly for the wide-flatting archetypes.
opener = {}


def on_decision(
    current_player, controller, action, raise_to, phase_name, gs, sim_current_street, decision
):
    arch = name_to_arch.get(current_player.name)
    if arch is None:
        return
    flags = hand_flags.setdefault(
        current_player.name, {'vpip': False, 'pfr': False, 'all_in': False}
    )
    if action == 'all_in':
        flags['all_in'] = True
    if phase_name == 'PRE_FLOP':
        if action in ('call', 'raise', 'bet', 'all_in'):
            flags['vpip'] = True
        if action in ('raise', 'all_in'):
            flags['pfr'] = True
        scenario, _, _ = classify_preflop_scenario(gs)
        if scenario == 'rfi' and action in ('raise', 'all_in'):
            opener.setdefault('_opener', current_player.name)
        node = nodes[arch][scenario]
        node[0] += 1
        if action in ('raise', 'all_in'):
            node[1] += 1
        if action == 'fold':
            node[2] += 1
        # Opener-conditioned vs_3bet (the clean fourbet / fold_to_3bet spot).
        if scenario == 'vs_3bet' and opener.get('_opener') == current_player.name:
            op = nodes[arch]['vs_3bet_op']
            op[0] += 1
            if action in ('raise', 'all_in'):
                op[1] += 1
            if action == 'fold':
                op[2] += 1
    else:
        if action in ('raise', 'all_in', 'bet'):
            pf[arch]['agg'] += 1
        elif action == 'call':
            pf[arch]['call'] += 1


n_field = len(FIELD)
for hand_num in range(N_HANDS):
    hand_seed = BASE_SEED + hand_num
    random.seed(hand_seed)

    # Rotate one archetype out so 6 of 7 are seated; rotate seat order too so
    # position isn't fixed per archetype.
    sit_out = hand_num % n_field
    seated = [FIELD[(sit_out + 1 + i) % n_field] for i in range(6)]
    dealer_idx = hand_num % 6

    name_to_arch.clear()
    hand_flags.clear()
    opener.clear()
    all_names = []
    seat_configs = []
    for sim_key, target_key in seated:
        name = f'{target_key}_seat'
        all_names.append(name)
        name_to_arch[name] = target_key
        seat_configs.append((name, ARCHETYPES[sim_key]))

    gs = make_game_state(
        player_names=all_names,
        big_blind=BIG_BLIND,
        starting_stack=STARTING_STACK,
        dealer_idx=dealer_idx,
        seed=hand_seed,
    )
    sm = PokerStateMachine(gs)
    sm.current_hand_seed = hand_seed

    controllers = []
    for i, (name, cfg) in enumerate(seat_configs):
        controllers.append(
            make_controller(name, cfg, strategy_table, sm, rng_seed=hand_seed + 1_000_000 * (i + 1))
        )

    drive_hand(sm, controllers, hero_name=None, hero_controller=None, on_decision=on_decision)

    # Roll up per-hand flags.
    for name, flags in hand_flags.items():
        arch = name_to_arch[name]
        c = counts[arch]
        c['hands'] += 1
        c['vpip'] += 1 if flags['vpip'] else 0
        c['pfr'] += 1 if flags['pfr'] else 0
        c['all_in'] += 1 if flags['all_in'] else 0

    if (hand_num + 1) % 1500 == 0:
        print(f'... {hand_num + 1}/{N_HANDS} hands', flush=True)


def stats_for(arch):
    vs_open = nodes[arch]['vs_open']
    # fourbet / fold_to_3bet use the opener-conditioned node (excludes squeeze).
    vs_3bet = nodes[arch]['vs_3bet_op']
    c = counts[arch]
    hands = max(c['hands'], 1)
    return {
        'vpip': (pct(c['vpip'], hands), c['hands']),
        'pfr': (pct(c['pfr'], hands), c['hands']),
        'threebet': (pct(vs_open[1], vs_open[0]), vs_open[0]),
        'fourbet': (pct(vs_3bet[1], vs_3bet[0]), vs_3bet[0]),
        'fold_to_3bet': (pct(vs_3bet[2], vs_3bet[0]), vs_3bet[0]),
        'af': (pf[arch]['agg'] / max(pf[arch]['call'], 1), pf[arch]['agg'] + pf[arch]['call']),
        'all_in': (pct(c['all_in'], hands), c['hands']),
    }


MARK = {'pass': 'ok ', 'warn': 'WARN', 'fail': 'FAIL', 'low_n': 'low-n', 'no_data': '--'}


def fmt(stat, value):
    return f'{value:.2f}' if stat == 'af' else f'{value:.1f}'


print('\n==== ARCHETYPE VALIDATION (mixed field — 6 of 7 archetypes per hand) ====')
print(f'Hands: {N_HANDS}  seed base: {BASE_SEED}')
print('Realistic field: each archetype measured vs a rotating mix of the others.\n')

hdr = f"{'archetype':<16} {'stat':<14} {'actual':>8} {'target band':>14}  result"
print(hdr)
print('-' * len(hdr))
fails = []
for sim_key, target_key in FIELD:
    stats = stats_for(target_key)
    band_table = ARCHETYPE_TARGETS[target_key]
    for stat in STAT_ORDER:
        value, sample = stats[stat]
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
            f'  {arch:<16} {stat:<13} {fmt(stat, value)} {direction} '
            f'(band {fmt(stat, lo)}-{fmt(stat, hi)})'
        )
else:
    print('All scored stats within band or warn. No hard fails.')
