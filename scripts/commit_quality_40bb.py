"""40bb commit-quality probe — are the bots committing TRASH or value?

The prod symptom that launched the archetype-shaping workstream was specific:
"Q2o 4-bet-shoves, folding out AK." Earlier eval (SOLVER_CHART_SCOPE, Sweep A/D)
found shallow stacks DON'T blind-shove (jam% stays low) and the apparent
"collapse" was a Jeff_station measurement artifact — so before building any
depth-aware sizing fix we need to know whether, on CURRENT code at the casino's
40bb buy-in, the bots commit a reasonable value range or actually spew trash.

This probe seats the mixed field at 40bb and records, per archetype, the
hole-card RANGE the bots:
  * 4-bet with (as the RFI opener facing a 3-bet — the clean "4-bet" spot), and
  * get all-in with (any street).
Each committed hand is scored by its all-in equity-vs-a-random-hand (eval7 MC,
precomputed per canonical hand). Trash = low equity vs random. We report the
equity distribution + the specific low-equity hands committed, so "spew" is
visible, not inferred.
"""

import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '/app')

import eval7

from experiments._hand_loop import drive_hand
from experiments.simulate_bb100 import ARCHETYPES, make_controller, make_game_state
from poker.controllers import _get_canonical_hand, card_to_string
from poker.poker_state_machine import PokerStateMachine
from poker.strategy.preflop_classifier import classify_preflop_scenario
from poker.strategy.strategy_table import load_strategy_table

N_HANDS = 3000
BASE_SEED = 4242
BIG_BLIND = 100
STARTING_STACK = 4000  # 40bb — the casino MIN_BUY_IN_BB

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

# ── Precompute equity-vs-random per canonical hand (eval7 MC, seeded) ────────
_RANKS = 'AKQJT98765432'


def _combo(canon):
    if len(canon) == 2:  # pair
        return [eval7.Card(canon[0] + 'h'), eval7.Card(canon[0] + 's')]
    a, b, s = canon[0], canon[1], canon[2]
    return [eval7.Card(a + 'h'), eval7.Card(b + ('h' if s == 's' else 'd'))]


def _all_canon():
    out = []
    for i, a in enumerate(_RANKS):
        for j, b in enumerate(_RANKS):
            if i == j:
                out.append(a + b)
            elif i < j:
                out.append(a + b + 's')
                out.append(a + b + 'o')
    return out


def _equity_vs_random(canon, iters=1500):
    rng = random.Random(hash(canon) & 0xFFFF)
    hero = _combo(canon)
    known = set(hero)
    rest = [c for c in eval7.Deck().cards if c not in known]
    wins = ties = 0
    for _ in range(iters):
        rng.shuffle(rest)
        opp = rest[:2]
        board = rest[2:7]
        hv = eval7.evaluate(hero + board)
        ov = eval7.evaluate(opp + board)
        if hv > ov:
            wins += 1
        elif hv == ov:
            ties += 1
    return (wins + 0.5 * ties) / iters


print('precomputing equity-vs-random for 169 hands...', flush=True)
EQUITY = {c: _equity_vs_random(c) for c in _all_canon()}
print('done.\n', flush=True)

# ── Tally structures ────────────────────────────────────────────────────────
# arch -> {'fourbet': Counter(canon), 'allin': Counter(canon),
#          'fourbet_n': int (opener-faces-3bet denominator)}
tally = defaultdict(lambda: {'fourbet': Counter(), 'allin': Counter(), 'fourbet_n': 0})

name_to_arch = {}
opener = {}  # '_opener' -> name of the hand's RFI opener


def _canon_for(player):
    hole = [card_to_string(c) for c in player.hand] if getattr(player, 'hand', None) else []
    return _get_canonical_hand(hole) if hole else ''


def on_decision(
    current_player, controller, action, raise_to, phase_name, gs, sim_current_street, decision
):
    name = current_player.name
    arch = name_to_arch.get(name)
    if arch is None:
        return
    if action == 'all_in':
        canon = _canon_for(current_player)
        if canon:
            tally[arch]['allin'][canon] += 1
    if phase_name != 'PRE_FLOP':
        return
    scenario, _, _ = classify_preflop_scenario(gs)
    if scenario == 'rfi' and action in ('raise', 'all_in'):
        opener.setdefault('_opener', name)
    if scenario == 'vs_3bet' and opener.get('_opener') == name:
        tally[arch]['fourbet_n'] += 1
        if action in ('raise', 'all_in'):
            canon = _canon_for(current_player)
            if canon:
                tally[arch]['fourbet'][canon] += 1


n_field = len(FIELD)
for hand_num in range(N_HANDS):
    hand_seed = BASE_SEED + hand_num
    random.seed(hand_seed)
    sit_out = hand_num % n_field
    seated = [FIELD[(sit_out + 1 + i) % n_field] for i in range(6)]
    dealer_idx = hand_num % 6

    name_to_arch.clear()
    opener.clear()
    all_names, seat_configs = [], []
    for sim_key, target_key in seated:
        nm = f'{target_key}_seat'
        all_names.append(nm)
        name_to_arch[nm] = target_key
        seat_configs.append((nm, ARCHETYPES[sim_key]))

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
        make_controller(nm, cfg, strategy_table, sm, rng_seed=hand_seed + 1_000_000 * (i + 1))
        for i, (nm, cfg) in enumerate(seat_configs)
    ]
    drive_hand(sm, controllers, hero_name=None, hero_controller=None, on_decision=on_decision)
    if (hand_num + 1) % 1000 == 0:
        print(f'... {hand_num + 1}/{N_HANDS} hands', flush=True)


def _summary(counter):
    """(n, mean_equity, %weak<0.45, %trash<0.35, worst offenders list)."""
    items = list(counter.items())
    n = sum(c for _, c in items)
    if n == 0:
        return 0, None, None, None, []
    eqsum = sum(EQUITY[h] * c for h, c in items)
    weak = sum(c for h, c in items if EQUITY[h] < 0.45)
    trash = sum(c for h, c in items if EQUITY[h] < 0.35)
    worst = sorted(items, key=lambda x: EQUITY[x[0]])
    worst_lo = [(h, c, EQUITY[h]) for h, c in worst if EQUITY[h] < 0.45][:6]
    return n, eqsum / n, 100.0 * weak / n, 100.0 * trash / n, worst_lo


print('\n==== 40bb COMMIT-QUALITY (mixed field) ====')
print(f'Hands: {N_HANDS}  stack: {STARTING_STACK // BIG_BLIND}bb')
print('equity = hand-vs-random all-in equity (eval7). weak<0.45, trash<0.35.\n')
print('Note: 4-bets/commits face a 3-bet range (STRONGER than random), so even a')
print('0.45-0.55 hand is a marginal commit; <0.45 vs random is a real red flag.\n')

for sim_key, target_key in FIELD:
    t = tally[target_key]
    fn, fmean, fweak, ftrash, fworst = _summary(t['fourbet'])
    an, amean, aweak, atrash, aworst = _summary(t['allin'])
    fb_rate = (100.0 * fn / t['fourbet_n']) if t['fourbet_n'] else 0.0
    print(f'── {target_key} ──')
    print(
        f'   4-bet (as opener): n={fn}/{t["fourbet_n"]} ({fb_rate:.1f}% of opener-faces-3bet)'
        + (f'  meanEq={fmean:.2f}  weak={fweak:.0f}%  trash={ftrash:.0f}%' if fn else '  (none)')
    )
    if fworst:
        print(
            '       4-bet low-equity hands: ' + ', '.join(f'{h}×{c}({e:.2f})' for h, c, e in fworst)
        )
    print(
        f'   all-in (any street): n={an}'
        + (f'  meanEq={amean:.2f}  weak={aweak:.0f}%  trash={atrash:.0f}%' if an else '  (none)')
    )
    if aworst:
        print(
            '       all-in low-equity hands: '
            + ', '.join(f'{h}×{c}({e:.2f})' for h, c, e in aworst)
        )
    print()
