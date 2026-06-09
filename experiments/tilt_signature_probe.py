"""Within-spot PAIRED probe for the §4 tilt behavioral signature
(TILT_EXCURSION_DESIGN.md). The full on-vs-off game sim was confounded — the flag
changes decisions, so the two arms diverge into different trajectories and the
aggregate aggression rates aren't comparable. A paired probe sidesteps that: it
evaluates BOTH arms on the *same* decision spot (`reference_cash_sim_ab_paired`),
so the only thing that differs is the flag.

It runs each persona's anchors + a fixed set of representative baseline strategies
through the REAL `modify_strategy` pipeline (offsets → softmax → divergence clamp)
twice — TILT_SIGNATURE_ENABLED off then on — at a tilt state, and measures the
change in AGGRESSION MASS (Σ prob of aggressive actions). The deviation profile is
held constant across arms, so the delta isolates the signature's direction flip.

Expected (the signature working):
  - tilted, risk-averse (risk_identity < 0.5): delta < 0 (COLLAPSE — off leaned
    aggressive via the state map; on flips to passive).
  - shaken, risk-seeking (>= 0.5): delta > 0 (SPEW — off was passive via the state
    map; on flips to aggressive).
  - tilted risk-seeking / shaken risk-averse: ~0 (same direction both arms).

Run: docker compose exec -T backend python3 -m experiments.tilt_signature_probe
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Dict, List

from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import categorize_action, modify_strategy
from poker.strategy.strategy_profile import StrategyProfile
from poker.psychology_model import PersonalityAnchors

FLAG = 'TILT_SIGNATURE_ENABLED'
PROFILE = DEVIATION_PROFILES['tag']  # neutral, held constant across both arms
INTENSITY = 0.5  # representative moderate tilt

# Representative preflop baseline strategies (the "same spots" both arms see).
BASELINES: List[Dict[str, float]] = [
    {'fold': 0.30, 'call': 0.40, 'raise_2.5bb': 0.30},               # balanced open/defend
    {'fold': 0.20, 'call': 0.30, 'raise_2.5bb': 0.30, 'all_in': 0.20},  # aggressive spot
    {'fold': 0.50, 'call': 0.35, 'raise_2.5bb': 0.15},               # facing pressure
    {'fold': 0.15, 'call': 0.55, 'raise_2.5bb': 0.30},               # call-heavy
]


def _agg_mass(profile: StrategyProfile) -> float:
    return sum(
        p for a, p in profile.action_probabilities.items() if categorize_action(a) == 'aggressive'
    )


def _delta_for(anchors, state: str) -> float:
    """Mean (on − off) aggression-mass delta across the baseline spots, paired."""
    es = SimpleNamespace(state=state, intensity=INTENSITY, severity='moderate')
    deltas = []
    for spot in BASELINES:
        base = StrategyProfile(action_probabilities=dict(spot))
        legal = list(spot.keys())
        os.environ[FLAG] = '0'
        off, _ = modify_strategy(base, legal, anchors, es, PROFILE)
        os.environ[FLAG] = '1'
        on, _ = modify_strategy(base, legal, anchors, es, PROFILE)
        deltas.append(_agg_mass(on) - _agg_mass(off))
    return sum(deltas) / len(deltas)


def _anchors_of(cfg: dict) -> PersonalityAnchors:
    return PersonalityAnchors.from_dict(cfg['anchors'])


def main() -> None:
    with open('poker/personalities.json') as f:
        personas = json.load(f).get('personalities', {})
    real = {
        n: c for n, c in personas.items()
        if isinstance(c, dict) and 'anchors' in c
        and float(c['anchors'].get('recovery_rate', 0) or 0) > 0
    }

    tiers = [
        ('risk-averse <0.40', lambda r: r < 0.40),
        ('mid 0.40-0.60', lambda r: 0.40 <= r < 0.60),
        ('risk-seeking >=0.60', lambda r: r >= 0.60),
    ]

    print('=' * 78)
    print(f'TILT SIGNATURE — paired within-spot probe ({len(real)} personas, '
          f'intensity {INTENSITY})')
    print('  aggression-mass delta = Σp(aggressive)|on − |off, paired on the same spot')
    print('=' * 78)
    print(f'  {"tier":22s} {"n":>3s} {"Δagg TILTED":>12s} {"Δagg SHAKEN":>12s}')
    for label, pred in tiers:
        members = [(n, _anchors_of(c)) for n, c in real.items()
                   if pred(float(c['anchors'].get('risk_identity', 0.5)))]
        if not members:
            continue
        tilted = [_delta_for(a, 'tilted') for _, a in members]
        shaken = [_delta_for(a, 'shaken') for _, a in members]
        mt = sum(tilted) / len(tilted)
        ms = sum(shaken) / len(shaken)
        print(f'  {label:22s} {len(members):3d} {mt:+11.3f} {ms:+11.3f}')

    print('\n  EXPECT: risk-averse TILTED Δ<0 (collapse); risk-seeking SHAKEN Δ>0 (spew);')
    print('  the same-direction cells (risk-seeking tilted, risk-averse shaken) ≈ 0.')
    print('  Paired => trajectory-free; isolates the signature, not the game flow.')


if __name__ == '__main__':
    main()
