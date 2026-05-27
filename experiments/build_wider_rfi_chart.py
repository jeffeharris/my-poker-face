#!/usr/bin/env python3
"""Build poker/strategy/data/preflop_100bb_6max_wider_rfi.json.

A deep copy of preflop_100bb_6max.json with ONLY rfi.CO / rfi.BTN / rfi.SB
widened to GTO open frequencies (combo-weighted, pure-open):

    CO  ~27.6%   (this range: 27.3%)
    BTN ~47.5%   (this range: 47.5%, == TOP_55 tier)
    SB  ~39.4%   (this range: 40.3%)

Every hand in a widened position is pure: an open hand -> {"raise_2.5bb": 1.0},
everything else -> {"fold": 1.0}. rfi.UTG, rfi.HJ, and ALL of
vs_open/vs_3bet/vs_4bet are left BYTE-IDENTICAL to the base chart.

These are standard wide late-position GTO-shaped ranges (all pairs, all/most
suited, wide offsuit broadways + aces, suited connectors/gappers), NOT a raw
equity ranking.

Run inside the backend container:
    docker compose exec -T backend python -m experiments.build_wider_rfi_chart
"""

import json
import os

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'poker', 'strategy', 'data',
)
_BASE = os.path.join(_DATA_DIR, 'preflop_100bb_6max.json')
_OUT = os.path.join(_DATA_DIR, 'preflop_100bb_6max_wider_rfi.json')

# ── Wide GTO-shaped open ranges per widened position ────────────────────────

_PAIRS = ['AA', 'KK', 'QQ', 'JJ', 'TT', '99', '88', '77', '66', '55', '44', '33', '22']
_SUITED_A = ['AKs', 'AQs', 'AJs', 'ATs', 'A9s', 'A8s', 'A7s', 'A6s', 'A5s', 'A4s', 'A3s', 'A2s']

# CO ~27.6% — all pairs, all suited aces, suited kings to K4s, suited queens to
# Q7s, suited jacks to J8s, suited tens to T8s, suited connectors, offsuit
# broadways + A9o.
CO_OPEN = set(
    _PAIRS + _SUITED_A
    + ['KQs', 'KJs', 'KTs', 'K9s', 'K8s', 'K7s', 'K6s', 'K5s', 'K4s']
    + ['QJs', 'QTs', 'Q9s', 'Q8s', 'Q7s']
    + ['JTs', 'J9s', 'J8s']
    + ['T9s', 'T8s']
    + ['98s', '97s', '87s', '86s', '76s', '65s', '54s']
    + ['AKo', 'AQo', 'AJo', 'ATo', 'A9o']
    + ['KQo', 'KJo', 'KTo']
    + ['QJo', 'QTo']
    + ['JTo']
)

# BTN ~47.5% — the standard TOP_55 tier (93 hands), proven combo-weighted 47.5%.
BTN_OPEN = set(
    _PAIRS + _SUITED_A
    + ['KQs', 'KJs', 'KTs', 'K9s', 'K8s', 'K7s', 'K6s', 'K5s', 'K4s', 'K3s', 'K2s']
    + ['QJs', 'QTs', 'Q9s', 'Q8s', 'Q7s', 'Q6s', 'Q5s', 'Q4s', 'Q3s', 'Q2s']
    + ['JTs', 'J9s', 'J8s', 'J7s']
    + ['T9s', 'T8s', 'T7s']
    + ['98s', '97s', '87s', '86s', '76s', '75s', '65s', '64s', '54s', '53s', '43s']
    + ['AKo', 'AQo', 'AJo', 'ATo', 'A9o', 'A8o', 'A7o', 'A6o', 'A5o', 'A4o', 'A3o', 'A2o']
    + ['KQo', 'KJo', 'KTo', 'K9o', 'K8o', 'K7o', 'K6o', 'K5o']
    + ['QJo', 'QTo', 'Q9o', 'Q8o']
    + ['JTo', 'J9o']
    + ['T9o']
    + ['98o', '87o']
)

# SB ~39.4% — wide: all pairs, all suited aces/kings/queens (to Q5s), suited
# jacks to J7s, tens to T7s, full connectors+gappers, ALL offsuit aces, offsuit
# broadways.
SB_OPEN = set(
    _PAIRS + _SUITED_A
    + ['KQs', 'KJs', 'KTs', 'K9s', 'K8s', 'K7s', 'K6s', 'K5s', 'K4s', 'K3s', 'K2s']
    + ['QJs', 'QTs', 'Q9s', 'Q8s', 'Q7s', 'Q6s', 'Q5s']
    + ['JTs', 'J9s', 'J8s', 'J7s']
    + ['T9s', 'T8s', 'T7s']
    + ['98s', '97s', '87s', '86s', '76s', '75s', '65s', '64s', '54s', '53s', '43s']
    + ['AKo', 'AQo', 'AJo', 'ATo', 'A9o', 'A8o', 'A7o', 'A6o', 'A5o', 'A4o', 'A3o', 'A2o']
    + ['KQo', 'KJo', 'KTo', 'K9o']
    + ['QJo', 'QTo', 'Q9o']
    + ['JTo', 'J9o']
    + ['T9o']
)

_WIDENED = {'CO': CO_OPEN, 'BTN': BTN_OPEN, 'SB': SB_OPEN}


def _combos(hand: str) -> int:
    if len(hand) == 2:
        return 6
    return 4 if hand[2] == 's' else 12


def _rfi_pct(pos_dict: dict) -> float:
    tot = opened = 0.0
    for hand, actions in pos_dict.items():
        c = _combos(hand)
        tot += c
        opened += c * sum(p for a, p in actions.items() if a.startswith('raise') or a in ('all_in', 'jam'))
    return 100.0 * opened / tot if tot else 0.0


def main():
    with open(_BASE) as f:
        data = json.load(f)

    # Rewrite ONLY the three widened RFI positions, in place, preserving the key
    # set (all 169 hands) so the file shape matches the base exactly.
    for pos, open_set in _WIDENED.items():
        pos_dict = data['rfi'][pos]
        for hand in pos_dict:
            pos_dict[hand] = {'raise_2.5bb': 1.0} if hand in open_set else {'fold': 1.0}

    with open(_OUT, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

    print(f"wrote {_OUT}")
    for pos in ('UTG', 'HJ', 'CO', 'BTN', 'SB'):
        print(f"  rfi.{pos}: {_rfi_pct(data['rfi'][pos]):.1f}%")


if __name__ == '__main__':
    main()
