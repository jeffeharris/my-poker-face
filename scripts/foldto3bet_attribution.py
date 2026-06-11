"""Attribution probe: split fold_to_3bet into RFI-vs-3bet vs squeeze defense.

The `vs_3bet` node (preflop_classifier.classify_preflop_scenario) is classified
purely by raise count == 2 — it does NOT check that the acting player was the
original RFI raiser. So it conflates two very different spots:

  * RFI-vs-3bet : you raised first-in, someone 3-bet you. This is what the
    standard poker "Fold to 3-Bet" stat (and ARCHETYPE_TARGETS.fold_to_3bet)
    means.
  * squeeze     : you cold-CALLED an open, then someone 3-bet over the top — OR
    you're a blind facing open+3bet cold. A separate stat ("Fold to Squeeze").
    Loose-passive archetypes flat wide → they get squeezed with weak ranges →
    they fold that trash → fold_to_3bet inflates against a band that never meant
    to include these spots.

This probe seats the mixed field (6 of 7 archetypes per hand) and, per archetype,
splits every vs_3bet decision by whether the actor was the hand's RFI opener.
Reports fold% + frequency of each bucket, plus the band, so we can see whether the
RFI-vs-3bet fold rate alone is in band (→ fix the METRIC) or still too high (→ fix
the charts).
"""

import random
import sys
from collections import defaultdict

sys.path.insert(0, '/app')

from experiments._hand_loop import drive_hand
from experiments.simulate_bb100 import ARCHETYPES, make_controller, make_game_state
from poker.archetype_targets import ARCHETYPE_TARGETS
from poker.poker_state_machine import PokerStateMachine
from poker.strategy.preflop_classifier import classify_preflop_scenario
from poker.strategy.strategy_table import load_strategy_table

N_HANDS = 9000
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


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


# arch -> bucket -> [decisions, folds]; buckets: rfi_vs_3bet, squeeze_vs_3bet
buckets = defaultdict(lambda: defaultdict(lambda: [0, 0]))

name_to_arch = {}
# per-hand: player name -> True if they made the first preflop raise (RFI opener)
opener_this_hand = {}


def on_decision(
    current_player, controller, action, raise_to, phase_name, gs, sim_current_street, decision
):
    name = current_player.name
    arch = name_to_arch.get(name)
    if arch is None or phase_name != 'PRE_FLOP':
        return
    scenario, _, _ = classify_preflop_scenario(gs)
    # Record the RFI opener (first raise of the hand) BEFORE bucketing this action.
    if scenario == 'rfi' and action in ('raise', 'all_in'):
        opener_this_hand.setdefault('_opener', name)
    if scenario == 'vs_3bet':
        was_opener = opener_this_hand.get('_opener') == name
        b = buckets[arch]['rfi_vs_3bet' if was_opener else 'squeeze_vs_3bet']
        b[0] += 1
        if action == 'fold':
            b[1] += 1


n_field = len(FIELD)
for hand_num in range(N_HANDS):
    hand_seed = BASE_SEED + hand_num
    random.seed(hand_seed)
    sit_out = hand_num % n_field
    seated = [FIELD[(sit_out + 1 + i) % n_field] for i in range(6)]
    dealer_idx = hand_num % 6

    name_to_arch.clear()
    opener_this_hand.clear()
    all_names = []
    seat_configs = []
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

    if (hand_num + 1) % 1500 == 0:
        print(f'... {hand_num + 1}/{N_HANDS} hands', flush=True)


print('\n==== fold_to_3bet ATTRIBUTION (mixed field) — RFI-vs-3bet vs squeeze ====')
print(f'Hands: {N_HANDS}  seed base: {BASE_SEED}\n')
hdr = (
    f"{'archetype':<16} {'band':>9} | {'RFI-vs-3bet':>12} {'fold%':>7} {'n':>6} "
    f"| {'squeeze':>10} {'fold%':>7} {'n':>6} | {'combined':>9} {'sqz%share':>9}"
)
print(hdr)
print('-' * len(hdr))
for sim_key, target_key in FIELD:
    band = ARCHETYPE_TARGETS[target_key]['fold_to_3bet']
    rfi = buckets[target_key]['rfi_vs_3bet']
    sqz = buckets[target_key]['squeeze_vs_3bet']
    tot_n = rfi[0] + sqz[0]
    tot_f = rfi[1] + sqz[1]
    rfi_fold = pct(rfi[1], rfi[0])
    in_band = 'ok' if band[0] <= rfi_fold <= band[1] else ('HIGH' if rfi_fold > band[1] else 'LOW')
    print(
        f"{target_key:<16} {f'{band[0]:.0f}-{band[1]:.0f}':>9} | "
        f"{'fold/tot':>12} {rfi_fold:>6.1f} {rfi[0]:>6} "
        f"| {'':>10} {pct(sqz[1], sqz[0]):>6.1f} {sqz[0]:>6} "
        f"| {pct(tot_f, tot_n):>8.1f} {pct(sqz[0], tot_n):>8.1f}  [{in_band}]"
    )
print('\nKey: "combined" = the current (raise-count-only) fold_to_3bet metric.')
print('"sqz%share" = squeeze decisions as a fraction of all vs_3bet decisions.')
print('If RFI-vs-3bet fold% is in band but combined is HIGH → the METRIC over-counts.')
