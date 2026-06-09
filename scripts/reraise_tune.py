"""Tune lag/maniac reraise split to pull their 4-bet into band.

Opener-conditioned (post the #244 metric fix): lag 4-bet 24.6 (band 10-20), maniac
4-bet 48.5 (band 24-40 FAIL) — distortion-driven over-4-bet. The reraise split
(reraise_aggression_scale / reraise_max_per_action_shift) applies to ALL preflop
facing-raise nodes, so it scales 3-bet (vs_open) AND 4-bet (vs_3bet) together —
watch 3-bet doesn't fall out of band while pulling 4-bet down. Per the handoff,
the per-action CAP is usually the binding lever.

Runs the realistic mixed field; for each arm it patches lag+maniac's reraise
params and reports their opener-conditioned 3-bet/4-bet (the other 5 seats are
unchanged, providing the field). 4-bet/fold metrics are opener-conditioned.
"""

import dataclasses
import random
import sys
from collections import defaultdict

sys.path.insert(0, '/app')

from experiments._hand_loop import drive_hand
from experiments.simulate_bb100 import ARCHETYPES, make_controller, make_game_state
from poker.archetype_targets import ARCHETYPE_TARGETS
from poker.poker_state_machine import PokerStateMachine
from poker.strategy import deviation_profiles as dp
from poker.strategy.preflop_classifier import classify_preflop_scenario
from poker.strategy.strategy_table import load_strategy_table

N_HANDS = 3000
BASE_SEED = 4242
BIG_BLIND = 100
STARTING_STACK = 10000

FIELD = [
    ('Nit', 'nit'),
    ('Rock', 'rock'),
    ('TAG', 'tag'),
    ('LAG', 'lag'),
    ('Maniac', 'maniac'),
    ('Calling Station', 'calling_station'),
    ('WeakFish', 'weak_fish'),
]
strategy_table = load_strategy_table()

# (label, lag (scale,cap), maniac (scale,cap))
ARMS = [
    ('current   ', (0.60, 0.20), (0.90, 0.18)),
    ('A caps-    ', (0.60, 0.12), (0.90, 0.10)),
    ('B caps+sc- ', (0.45, 0.12), (0.70, 0.10)),
    ('C deeper   ', (0.45, 0.10), (0.60, 0.08)),
]


def run(lag_p, maniac_p):
    orig = {k: dp.DEVIATION_PROFILES[k] for k in ('lag', 'maniac')}
    dp.DEVIATION_PROFILES['lag'] = dataclasses.replace(
        orig['lag'], reraise_aggression_scale=lag_p[0], reraise_max_per_action_shift=lag_p[1]
    )
    dp.DEVIATION_PROFILES['maniac'] = dataclasses.replace(
        orig['maniac'],
        reraise_aggression_scale=maniac_p[0],
        reraise_max_per_action_shift=maniac_p[1],
    )
    try:
        # arch -> {'vo':[tot,agg], 'v3o':[tot,agg]}  (v3o = vs_3bet as opener)
        t = defaultdict(lambda: {'vo': [0, 0], 'v3o': [0, 0]})
        name_to_arch, opener = {}, {}

        def on_dec(cp, controller, action, raise_to, phase, gs, street, decision):
            arch = name_to_arch.get(cp.name)
            if arch not in ('lag', 'maniac') or phase != 'PRE_FLOP':
                return
            scen, _, _ = classify_preflop_scenario(gs)
            agg = 1 if action in ('raise', 'all_in') else 0
            if scen == 'rfi' and agg:
                opener.setdefault('_o', {})[cp.name] = True
            if scen == 'vs_open':
                t[arch]['vo'][0] += 1
                t[arch]['vo'][1] += agg
            elif scen == 'vs_3bet' and opener.get('_o', {}).get(cp.name):
                t[arch]['v3o'][0] += 1
                t[arch]['v3o'][1] += agg

        nf = len(FIELD)
        for h in range(N_HANDS):
            seed = BASE_SEED + h
            random.seed(seed)
            seated = [FIELD[(h % nf + 1 + i) % nf] for i in range(6)]
            name_to_arch.clear()
            opener.clear()
            names, cfgs = [], []
            for sk, tk in seated:
                nm = f'{tk}_seat'
                names.append(nm)
                name_to_arch[nm] = tk
                cfgs.append((nm, ARCHETYPES[sk]))
            gs = make_game_state(
                player_names=names,
                big_blind=BIG_BLIND,
                starting_stack=STARTING_STACK,
                dealer_idx=h % 6,
                seed=seed,
            )
            sm = PokerStateMachine(gs)
            sm.current_hand_seed = seed
            ctrls = [
                make_controller(nm, cfg, strategy_table, sm, rng_seed=seed + 1_000_000 * (i + 1))
                for i, (nm, cfg) in enumerate(cfgs)
            ]
            drive_hand(sm, ctrls, hero_name=None, hero_controller=None, on_decision=on_dec)
        return t
    finally:
        dp.DEVIATION_PROFILES['lag'] = orig['lag']
        dp.DEVIATION_PROFILES['maniac'] = orig['maniac']


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


def band(arch, stat, v):
    lo, hi = ARCHETYPE_TARGETS[arch][stat]
    return 'ok ' if lo <= v <= hi else ('HIGH' if v > hi else 'LOW ')


print('==== reraise-split tune (mixed field, opener-conditioned) ====')
print(
    f'N={N_HANDS}/arm  | lag 3bet band {ARCHETYPE_TARGETS["lag"]["threebet"]} '
    f'4bet {ARCHETYPE_TARGETS["lag"]["fourbet"]} | maniac 3bet '
    f'{ARCHETYPE_TARGETS["maniac"]["threebet"]} 4bet {ARCHETYPE_TARGETS["maniac"]["fourbet"]}\n'
)
hdr = f"{'arm':<11} | {'lag scale/cap':>13} {'mnc scale/cap':>13} | {'lag3b':>6} {'lag4b':>6} | {'mnc3b':>6} {'mnc4b':>6}"
print(hdr)
print('-' * len(hdr))
for label, lp, mp in ARMS:
    t = run(lp, mp)
    l3 = pct(t['lag']['vo'][1], t['lag']['vo'][0])
    l4 = pct(t['lag']['v3o'][1], t['lag']['v3o'][0])
    m3 = pct(t['maniac']['vo'][1], t['maniac']['vo'][0])
    m4 = pct(t['maniac']['v3o'][1], t['maniac']['v3o'][0])
    print(
        f"{label} | {str(lp):>13} {str(mp):>13} | "
        f"{l3:5.1f}{band('lag','threebet',l3)[0]} {l4:5.1f}{band('lag','fourbet',l4)[0]} | "
        f"{m3:5.1f}{band('maniac','threebet',m3)[0]} {m4:5.1f}{band('maniac','fourbet',m4)[0]}",
        flush=True,
    )
print('\n(band marks: o=ok H=HIGH L=LOW; targets in header)')
