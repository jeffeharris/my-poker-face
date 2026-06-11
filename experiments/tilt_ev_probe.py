"""Phase-1 EV estimator for the §4 tilt behavioral signature
(docs/plans/TILT_EV_HARNESS.md, approach C). This is `tilt_signature_probe.py`
plus an EV estimator: instead of only reporting the aggression-mass DIRECTION and
the KL exploitability budget, it prices each arm's strategy in **bb** and reports
the paired ΔEV (on − off) per tilted spot.

WHAT THIS IS (and is not):
  - It is the instrument shakedown: it proves the EV machinery works end-to-end,
    pins the SIGN and rough MAGNITUDE of the signature's EV impact, and shows the
    fish-vs-competent-backdrop contrast.
  - It is NOT a trustworthy absolute bb/100. The spots here are REPRESENTATIVE but
    SYNTHETIC — their geometry (hero hand, pot, cost, sizing) is hand-authored, so
    the absolute bb depends on choices made here, not on real play. Phase-2 (the
    real build) replaces these spots with a corpus RECORDED from a psychology-on
    sim (`tilt_persistence_check.json`) so the spots are the ones the bot actually
    reaches. See the plan doc.

METHOD (paired, trajectory-free — same as the signature probe):
  For each persona and each spot, run the REAL `modify_strategy` pipeline twice on
  the *identical* spot — TILT_SIGNATURE_ENABLED off then on, at a tilted state —
  and price both resulting action distributions. ΔEV = EV(on) − EV(off) in bb. The
  spot (cards, pot, opponent) is held fixed across arms, so the only difference is
  the flag.

EV MODEL (Phase-1, heads-up, forward EV from the decision point, in bb):
  - fold        : 0
  - call        : eq·(pot + cost) − cost
  - raise/all_in: f·pot + (1 − f)·(eq·(pot + 2R) − R)
      where R = the raise increment (bb), f = villain's fold-to-raise frequency.
  eq = hero showdown equity (eval7 Monte-Carlo vs one random hand). The two
  BACKDROPS differ only in f: a fish rarely folds to a raise (aggression gets
  called → paid off or punished), a competent opp folds more (raises pick up dead
  money). Holding eq fixed across backdrops isolates the fold-equity channel, which
  is the dominant and well-understood difference (a caller is a donor; passivity is
  punished).

KNOWN LIMITATION (surfaced by this Phase-1 run — a HARD Phase-2 requirement, not a
  refinement): `eq` is the hero's equity vs a RANDOM hand, NOT conditioned on the
  villain CALLING. Heads-up, even trash has ~37% vs random, so with any fold-equity
  the EV model prices aggression as +EV almost everywhere — which makes the SPEW
  direction (risk-seekers shoving light when shaken) read spuriously +EV. The
  COLLAPSE direction (risk-averse trading aggression for passivity) is priced
  plausibly because it turns on forgone fold-equity, which the model DOES capture.
  Fix for Phase-2: eq-when-called must use the villain's CONTINUE range (top of
  range, far stronger than random) — that is what makes light aggression −EV. So
  this Phase-1 number is trustworthy for the collapse SIGN/MAGNITUDE only, and the
  EV machinery is validated end-to-end; the spew sign awaits range-aware equity.

Run: docker compose exec -T backend python3 -m experiments.tilt_ev_probe
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Dict, List

from poker.decision_analyzer import DecisionAnalyzer
from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import categorize_action, modify_strategy
from poker.strategy.strategy_profile import StrategyProfile

FLAG = 'TILT_SIGNATURE_ENABLED'
PROFILE = DEVIATION_PROFILES['tag']  # neutral, held constant across both arms
INTENSITY = 0.5  # representative moderate tilt

_DA = DecisionAnalyzer(iterations=4000)
_EQ_CACHE: Dict[str, float] = {}


def _equity(hero: List[str], board: List[str]) -> float:
    key = ''.join(sorted(hero)) + '|' + ''.join(sorted(board))
    if key not in _EQ_CACHE:
        # seed fixed => reproducible; vs one random hand (HU showdown baseline).
        _EQ_CACHE[key] = _DA.calculate_equity_vs_random(hero, board, 1, seed=1234) or 0.0
    return _EQ_CACHE[key]


# A spot is a fixed decision context + the baseline (pre-emotion) strategy the bot
# would play there. `actions` maps each action key to (kind, R_bb): kind in
# {fold, call, raise}; R_bb is the call cost (call) or raise increment (raise).
SPOTS = [
    {
        'label': 'balanced open/defend (KJs)',
        'hero': ['Kh', 'Jh'],
        'board': [],
        'pot': 3.5,
        'baseline': {'fold': 0.30, 'call': 0.40, 'raise_2.5bb': 0.30},
        'actions': {'fold': ('fold', 0.0), 'call': ('call', 1.5), 'raise_2.5bb': ('raise', 6.0)},
    },
    {
        'label': 'aggressive spot (AQo, 30bb)',
        'hero': ['Ah', 'Qd'],
        'board': [],
        'pot': 6.0,
        'baseline': {'fold': 0.20, 'call': 0.30, 'raise_2.5bb': 0.30, 'all_in': 0.20},
        'actions': {
            'fold': ('fold', 0.0),
            'call': ('call', 2.0),
            'raise_2.5bb': ('raise', 5.0),
            'all_in': ('raise', 28.0),
        },
    },
    {
        'label': 'facing 3bet pressure (99)',
        'hero': ['9h', '9c'],
        'board': [],
        'pot': 12.0,
        'baseline': {'fold': 0.50, 'call': 0.35, 'raise_2.5bb': 0.15},
        'actions': {'fold': ('fold', 0.0), 'call': ('call', 6.0), 'raise_2.5bb': ('raise', 12.0)},
    },
    {
        'label': 'call-heavy spot (87s)',
        'hero': ['8s', '7s'],
        'board': [],
        'pot': 4.0,
        'baseline': {'fold': 0.15, 'call': 0.55, 'raise_2.5bb': 0.30},
        'actions': {'fold': ('fold', 0.0), 'call': ('call', 1.0), 'raise_2.5bb': ('raise', 3.0)},
    },
]

# Backdrop = villain fold-to-raise frequency (the fold-equity channel).
BACKDROPS = {'fish (rarely folds)': 0.08, 'competent (folds more)': 0.42}


def _action_ev(kind: str, R: float, pot: float, eq: float, fold_to_raise: float) -> float:
    if kind == 'fold':
        return 0.0
    if kind == 'call':
        return eq * (pot + R) - R
    # raise
    return fold_to_raise * pot + (1.0 - fold_to_raise) * (eq * (pot + 2.0 * R) - R)


def _strategy_ev(profile: StrategyProfile, spot: dict, fold_to_raise: float) -> float:
    """Σ p(action)·EV(action), in bb, for one spot under one backdrop."""
    eq = _equity(spot['hero'], spot['board'])
    pot = spot['pot']
    total = 0.0
    for action, p in profile.action_probabilities.items():
        if p <= 0:
            continue
        kind, R = spot['actions'].get(action, ('fold', 0.0))
        total += p * _action_ev(kind, R, pot, eq, fold_to_raise)
    return total


def _agg_mass(profile: StrategyProfile) -> float:
    return sum(
        p for a, p in profile.action_probabilities.items() if categorize_action(a) == 'aggressive'
    )


def _measure(anchors, fold_to_raise: float, state: str):
    """Per-persona, paired across spots: returns (mean ΔEV on−off bb, mean Δagg)."""
    es = SimpleNamespace(state=state, intensity=INTENSITY, severity='moderate')
    d_ev, d_agg = [], []
    for spot in SPOTS:
        base = StrategyProfile(action_probabilities=dict(spot['baseline']))
        legal = list(spot['baseline'].keys())
        os.environ[FLAG] = '0'
        off, _ = modify_strategy(base, legal, anchors, es, PROFILE)
        os.environ[FLAG] = '1'
        on, _ = modify_strategy(base, legal, anchors, es, PROFILE)
        d_ev.append(_strategy_ev(on, spot, fold_to_raise) - _strategy_ev(off, spot, fold_to_raise))
        d_agg.append(_agg_mass(on) - _agg_mass(off))
    n = len(SPOTS)
    return sum(d_ev) / n, sum(d_agg) / n


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

    print('=' * 92)
    print(
        f'TILT SIGNATURE — Phase-1 EV probe ({len(real)} personas, intensity {INTENSITY}, tilted)'
    )
    print(
        '  ΔEV = mean per-spot EV(on) − EV(off) in bb, paired (trajectory-free). SYNTHETIC spots —'
    )
    print(
        '  magnitude/sign only, not a trustworthy absolute bb/100 (Phase-2 = real recorded spots).'
    )
    print('=' * 92)
    for state in ('tilted', 'shaken'):
        for bd_label, f in BACKDROPS.items():
            print(f'\n  [{state}] backdrop: {bd_label}  (villain fold-to-raise = {f:.2f})')
            print(f'    {"tier":22s} {"n":>3s} {"ΔEV (bb/spot)":>14s} {"Δagg":>8s}')
            for label, pred in tiers:
                members = [
                    _anchors_of(c)
                    for c in real.values()
                    if pred(float(c['anchors'].get('risk_identity', 0.5)))
                ]
                if not members:
                    continue
                rows = [_measure(a, f, state) for a in members]
                m = len(rows)
                dev = sum(r[0] for r in rows) / m
                dagg = sum(r[1] for r in rows) / m
                print(f'    {label:22s} {m:3d} {dev:+14.4f} {dagg:+8.3f}')

    print(
        '\n  READING IT: COLLAPSE (risk-averse, Δagg<0) is priced plausibly — small −EV vs a fish'
    )
    print('  (passivity forgoes value a fish would pay off), ~0 vs a competent opp. SPEW (risk-')
    print('  seeking, Δagg>0 when shaken) reads +EV here, but that is a MODEL ARTIFACT: eq is vs a')
    print('  RANDOM hand, so HU aggression is mechanically +EV (even 72o is ~37% vs random). Light')
    print('  spew is −EV only vs the villain CONTINUE range — the Phase-2 requirement (range-aware')
    print('  eq-when-called). So trust the collapse sign/magnitude; the spew sign awaits Phase-2.')
    print('  Headline once Phase-2 lands: per-spot bb × live tilted-decision rate (hothead ~12%')
    print('  per-hand, EMOTIONAL_SYSTEM_ANALYSIS §7) => bb/100 attributable to the signature.')


if __name__ == '__main__':
    main()
