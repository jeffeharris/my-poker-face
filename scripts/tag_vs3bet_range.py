"""Range-quality A/B for tag's defend_3bet: does it defend SANELY or flat trash?

Codex's caveat on the defend_3bet tendency: aggregate bands can pass while range
quality degrades — the fold→call / 4bet→call routing could flatten hands that
should stay value/bluff 4-bets, or call hands that should fold. So measure the
actual hole-card composition of tag's vs_3bet response (as the RFI opener),
scored by equity-vs-random (eval7), with the tendency ON vs OFF.

Healthy = the CALL (flat) range tag gains is decent hands (pairs/broadways/suited),
not 72o-class trash; the 4-bet range stays value-weighted.
"""

import dataclasses
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '/app')

import eval7

from experiments._hand_loop import drive_hand
from experiments.simulate_bb100 import ARCHETYPES, make_controller, make_game_state
from poker.controllers import _get_canonical_hand, card_to_string
from poker.poker_state_machine import PokerStateMachine
from poker.strategy import deviation_profiles as dp
from poker.strategy.preflop_classifier import classify_preflop_scenario
from poker.strategy.strategy_table import load_strategy_table

N_HANDS = 6000
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

# ── equity-vs-random per canonical hand (eval7 MC) ───────────────────────────
_RANKS = 'AKQJT98765432'


def _combo(c):
    if len(c) == 2:
        return [eval7.Card(c[0] + 'h'), eval7.Card(c[0] + 's')]
    return [eval7.Card(c[0] + 'h'), eval7.Card(c[1] + ('h' if c[2] == 's' else 'd'))]


def _all_canon():
    out = []
    for i, a in enumerate(_RANKS):
        for j, b in enumerate(_RANKS):
            if i == j:
                out.append(a + b)
            elif i < j:
                out += [a + b + 's', a + b + 'o']
    return out


def _eq(c, iters=1500):
    rng = random.Random(sum(ord(x) * (k + 1) for k, x in enumerate(c)))
    hero = _combo(c)
    rest = [x for x in eval7.Deck().cards if x not in set(hero)]
    w = t = 0
    for _ in range(iters):
        rng.shuffle(rest)
        hv, ov = eval7.evaluate(hero + rest[2:7]), eval7.evaluate(rest[:2] + rest[2:7])
        if hv > ov:
            w += 1
        elif hv == ov:
            t += 1
    return (w + 0.5 * t) / iters


print('precomputing equity...', flush=True)
EQUITY = {c: _eq(c) for c in _all_canon()}
print('done.\n', flush=True)


def run(tag_defend_on: bool):
    """Run the mixed field; tally tag's vs_3bet-as-opener action by hand."""
    # Patch tag's profile for this arm (DeviationProfile is frozen → replace).
    orig = dp.DEVIATION_PROFILES['tag']
    dp.DEVIATION_PROFILES['tag'] = dataclasses.replace(
        orig, spot_tendencies=orig.spot_tendencies if tag_defend_on else ()
    )
    try:
        # action -> Counter(canon); only tag, only vs_3bet, only as RFI opener.
        acts = defaultdict(Counter)
        name_to_arch, opener = {}, {}

        def on_decision(cp, controller, action, raise_to, phase, gs, street, decision):
            if name_to_arch.get(cp.name) != 'tag' or phase != 'PRE_FLOP':
                return
            scen, _, _ = classify_preflop_scenario(gs)
            if scen == 'rfi' and action in ('raise', 'all_in'):
                opener.setdefault('_o', cp.name)
            if scen == 'vs_3bet' and opener.get('_o') == cp.name:
                hole = [card_to_string(c) for c in cp.hand] if getattr(cp, 'hand', None) else []
                canon = _get_canonical_hand(hole) if hole else ''
                if canon:
                    bucket = 'raise' if action in ('raise', 'all_in') else action
                    acts[bucket][canon] += 1

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
            drive_hand(sm, ctrls, hero_name=None, hero_controller=None, on_decision=on_decision)
        return acts
    finally:
        dp.DEVIATION_PROFILES['tag'] = orig


def summarize(acts, label):
    fold = sum(acts['fold'].values())
    call = sum(acts['call'].values())
    raise_ = sum(acts['raise'].values())
    tot = fold + call + raise_

    def meaneq(counter):
        n = sum(counter.values())
        return (sum(EQUITY[h] * c for h, c in counter.items()) / n) if n else 0.0

    def worst(counter, k=8):
        return sorted(counter.items(), key=lambda x: EQUITY[x[0]])[:k]

    print(f'── {label} (n={tot} vs_3bet-as-opener) ──')
    print(
        f'   fold {100 * fold / tot:.1f}%  call {100 * call / tot:.1f}%  '
        f'4bet {100 * raise_ / tot:.1f}%'
    )
    print(
        f'   CALL range: meanEq={meaneq(acts["call"]):.2f}  '
        f'%weak(<0.45)={100 * sum(c for h, c in acts["call"].items() if EQUITY[h] < 0.45) / max(call, 1):.0f}%'
    )
    cw = worst(acts['call'])
    print('     lowest-equity CALLs: ' + ', '.join(f'{h}×{c}({EQUITY[h]:.2f})' for h, c in cw))
    print(
        f'   4BET range: meanEq={meaneq(acts["raise"]):.2f}  '
        f'%weak(<0.45)={100 * sum(c for h, c in acts["raise"].items() if EQUITY[h] < 0.45) / max(raise_, 1):.0f}%'
    )
    print()


print('==== tag vs_3bet RANGE QUALITY (defend_3bet ON vs OFF) ====')
print(f'Hands: {N_HANDS}  (tag, as RFI opener facing a 3-bet)\n')
off = run(tag_defend_on=False)
summarize(off, 'defend_3bet OFF')
on = run(tag_defend_on=True)
summarize(on, 'defend_3bet ON')

# Hands tag now CALLS that it FOLDED before (the newly-defended range).
newly_called = Counter()
for h, c in on['call'].items():
    delta = c - off['call'].get(h, 0)
    if delta > 0:
        newly_called[h] = delta
nc_n = sum(newly_called.values())
if nc_n:
    eqm = sum(EQUITY[h] * c for h, c in newly_called.items()) / nc_n
    weak = 100 * sum(c for h, c in newly_called.items() if EQUITY[h] < 0.45) / nc_n
    print(
        f'NEWLY-DEFENDED (call ON − call OFF): n={nc_n}  meanEq={eqm:.2f}  %weak(<0.45)={weak:.0f}%'
    )
    top = sorted(newly_called.items(), key=lambda x: -x[1])[:12]
    print('  most-added calls: ' + ', '.join(f'{h}×{c}({EQUITY[h]:.2f})' for h, c in top))
