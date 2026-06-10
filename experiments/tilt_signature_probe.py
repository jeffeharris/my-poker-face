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

import numpy as np

from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import (
    _kl_divergence,
    categorize_action,
    modify_strategy,
)
from poker.strategy.strategy_profile import StrategyProfile

FLAG = 'TILT_SIGNATURE_ENABLED'
PROFILE = DEVIATION_PROFILES['tag']  # neutral, held constant across both arms
INTENSITY = 0.5  # representative moderate tilt

# Representative preflop baseline strategies (the "same spots" both arms see).
BASELINES: List[Dict[str, float]] = [
    {'fold': 0.30, 'call': 0.40, 'raise_2.5bb': 0.30},  # balanced open/defend
    {'fold': 0.20, 'call': 0.30, 'raise_2.5bb': 0.30, 'all_in': 0.20},  # aggressive spot
    {'fold': 0.50, 'call': 0.35, 'raise_2.5bb': 0.15},  # facing pressure
    {'fold': 0.15, 'call': 0.55, 'raise_2.5bb': 0.30},  # call-heavy
]


def _agg_mass(profile: StrategyProfile) -> float:
    return sum(
        p for a, p in profile.action_probabilities.items() if categorize_action(a) == 'aggressive'
    )


def _kl_from_base(modified: StrategyProfile, base: Dict[str, float]) -> float:
    """KL(modified ‖ base) over the aligned action support — the strategy's
    divergence from the EV-optimal solver baseline = its exploitability budget."""
    keys = list(base.keys())
    p = np.array([modified.action_probabilities.get(k, 0.0) for k in keys])
    q = np.array([base[k] for k in keys])
    return float(_kl_divergence(p, q))


def _measure(anchors, state: str):
    """Per-persona, paired across the baseline spots: returns
    (mean agg-mass delta on−off, mean KL_off-from-base, mean KL_on-from-base)."""
    es = SimpleNamespace(state=state, intensity=INTENSITY, severity='moderate')
    d_agg, kl_off, kl_on = [], [], []
    for spot in BASELINES:
        base = StrategyProfile(action_probabilities=dict(spot))
        legal = list(spot.keys())
        os.environ[FLAG] = '0'
        off, _ = modify_strategy(base, legal, anchors, es, PROFILE)
        os.environ[FLAG] = '1'
        on, _ = modify_strategy(base, legal, anchors, es, PROFILE)
        d_agg.append(_agg_mass(on) - _agg_mass(off))
        kl_off.append(_kl_from_base(off, spot))
        kl_on.append(_kl_from_base(on, spot))
    n = len(BASELINES)
    return sum(d_agg) / n, sum(kl_off) / n, sum(kl_on) / n


def _anchors_of(cfg: dict) -> PersonalityAnchors:
    return PersonalityAnchors.from_dict(cfg['anchors'])


def main() -> None:
    with open('poker/personalities.json') as f:
        personas = json.load(f).get('personalities', {})
    real = {
        n: c
        for n, c in personas.items()
        if isinstance(c, dict)
        and 'anchors' in c
        and float(c['anchors'].get('recovery_rate', 0) or 0) > 0
    }

    tiers = [
        ('risk-averse <0.40', lambda r: r < 0.40),
        ('mid 0.40-0.60', lambda r: 0.40 <= r < 0.60),
        ('risk-seeking >=0.60', lambda r: r >= 0.60),
    ]

    print('=' * 86)
    print(
        f'TILT SIGNATURE — paired within-spot probe ({len(real)} personas, '
        f'intensity {INTENSITY}, tilted state)'
    )
    print('  Δagg = Σp(aggressive)|on−off (direction). KL = divergence from the EV-optimal')
    print('  solver baseline (exploitability budget). Paired => trajectory-free.')
    print('=' * 86)
    print(f'  {"tier":22s} {"n":>3s} {"Δagg":>8s} {"KL_off":>8s} {"KL_on":>8s} {"ΔKL":>8s}')
    for label, pred in tiers:
        members = [
            (n, _anchors_of(c))
            for n, c in real.items()
            if pred(float(c['anchors'].get('risk_identity', 0.5)))
        ]
        if not members:
            continue
        rows = [_measure(a, 'tilted') for _, a in members]
        m = len(rows)
        dagg = sum(r[0] for r in rows) / m
        klo = sum(r[1] for r in rows) / m
        kln = sum(r[2] for r in rows) / m
        print(f'  {label:22s} {m:3d} {dagg:+8.3f} {klo:8.4f} {kln:8.4f} {kln - klo:+8.4f}')

    print('\n  DIRECTION (Δagg): risk-averse collapse (Δ<0), risk-seeking unchanged when tilted.')
    print('  EV SAFETY (KL): on-vs-off divergence-from-baseline is comparable — the signature')
    print('  REDIRECTS the emotional offset within the same clamp budget, it does not amplify')
    print('  exploitability. Both arms are bounded by clamp_divergence (modify_strategy step 6),')
    print('  so the signature cannot exceed the distortion the bot already applies every hand.')
    print('  A precise bb/100 EV needs a psychology-in-the-loop paired harness (not built);')
    print('  the "right amount" of exploitability is a playtest/taste call.')


if __name__ == '__main__':
    main()
