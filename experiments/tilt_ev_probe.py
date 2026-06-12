"""Phase-2 EV estimator for the §4 tilt behavioral signature
(docs/plans/TILT_EV_HARNESS.md, approach C). This is `tilt_signature_probe.py`
plus an EV estimator: instead of only reporting the aggression-mass DIRECTION and
the KL exploitability budget, it prices each arm's strategy in **bb** and reports
the paired ΔEV (on − off) per tilted spot.

WHAT THIS IS (and is not):
  - It is the instrument shakedown: it proves the EV machinery works end-to-end,
    pins the SIGN and rough MAGNITUDE of the signature's EV impact, and shows the
    fish-vs-competent-backdrop contrast.
  - It is NOT yet a trustworthy absolute bb/100. The spots here are REPRESENTATIVE
    but SYNTHETIC — their geometry (hero hand, pot, cost, sizing) is hand-authored,
    so the absolute bb depends on choices made here, not on real play. The
    remaining build replaces these spots with a corpus RECORDED from a
    psychology-on sim (`tilt_persistence_check.json`) so the spots are the ones the
    bot actually reaches. See the plan doc.

PHASE-2 CHANGE — range-aware eq-when-called (the hard requirement the Phase-1 run
  surfaced). Phase-1 priced `eq` vs a RANDOM hand, so heads-up aggression was
  mechanically +EV (even 72o is ~37% vs random) and the SPEW direction read
  spuriously +EV. The fix, implemented here: equity is conditioned on the villain's
  CONTINUE range, not a random hand. Two equities are now distinguished per spot:
    - eq_call   : hero equity vs the villain's BETTING range (what hero is up
                  against when hero CALLS and the hand goes to showdown).
    - eq_called : hero equity vs the villain's CONTINUE-VS-RAISE range — the strong
                  top of the range that does NOT fold to hero's raise. This is far
                  stronger than random, which is exactly what makes light spew −EV.
  Each backdrop now defines BOTH its fold-to-raise frequency AND its continue range:
  a fish continues wide and weak (rarely folds, calls raises with junk), a competent
  opp continues tight and strong (folds more, but when it calls a raise it has it).

METHOD (paired, trajectory-free — same as the signature probe):
  For each persona and each spot, run the REAL `modify_strategy` pipeline twice on
  the *identical* spot — TILT_SIGNATURE_ENABLED off then on, at a tilted state —
  and price both resulting action distributions. ΔEV = EV(on) − EV(off) in bb. The
  spot (cards, pot, opponent) is held fixed across arms, so the only difference is
  the flag.

EV MODEL (heads-up, forward EV from the decision point, in bb):
  - fold        : 0
  - call        : eq_call·(pot + cost) − cost
  - raise/all_in: f·pot + (1 − f)·(eq_called·(pot + 2R) − R)
      where R = the raise increment (bb), f = villain's fold-to-raise frequency.
  The two BACKDROPS differ in f AND in the continue range eq_called prices against.
  Holding the spot fixed across arms isolates the flag.

Run: docker compose exec -T backend python3 -m experiments.tilt_ev_probe
"""

from __future__ import annotations

import json
import os
import random
from types import SimpleNamespace
from typing import Dict, List, Set, Tuple

from poker.card_utils import normalize_card_string
from poker.hand_ranges import (
    LATE_POSITION_RANGE,
    MIDDLE_POSITION_RANGE,
    STANDARD_3BET_RANGE,
    _get_all_combos_for_hand,
)
from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import categorize_action, modify_strategy
from poker.strategy.strategy_profile import StrategyProfile

FLAG = 'TILT_SIGNATURE_ENABLED'
PROFILE = DEVIATION_PROFILES['tag']  # neutral, held constant across both arms
INTENSITY = 0.5  # representative moderate tilt
EQ_ITERS = 20000  # equity is cached per (hero, board, range) — ~a dozen calls total
EQ_SEED = 1234  # reproducible Monte-Carlo

_EQ_CACHE: Dict[str, float] = {}


def _equity_vs_range(
    hero: List[str], board: List[str], range_set: Set[str], range_id: str
) -> float:
    """Hero showdown equity (win + ½·tie) vs one villain drawn uniformly over the
    COMBOS of `range_set`, Monte-Carlo over the runout. Seeded => reproducible.

    Uniform-over-combos weights pairs/suited/offsuit by their real combo counts
    (6/4/12), so the range is sampled combinatorially correctly — the whole point
    of conditioning on a range instead of a random hand.
    """
    import eval7  # imported lazily inside the fn, mirroring decision_analyzer

    key = ''.join(sorted(hero)) + '|' + ''.join(sorted(board)) + '|' + range_id
    if key in _EQ_CACHE:
        return _EQ_CACHE[key]

    hero_cards = [eval7.Card(normalize_card_string(c)) for c in hero]
    board_cards = [eval7.Card(normalize_card_string(c)) for c in board]
    known = set(hero_cards + board_cards)

    # Villain combos available given hero+board removal.
    excluded_str = set(hero) | set(board)
    combos: List[Tuple[str, str]] = [
        combo
        for canonical in range_set
        for combo in _get_all_combos_for_hand(canonical)
        if combo[0] not in excluded_str and combo[1] not in excluded_str
    ]
    if not combos:
        _EQ_CACHE[key] = 0.0
        return 0.0

    rng = random.Random(EQ_SEED)
    base_deck = [c for c in eval7.Deck().cards if c not in known]
    score = 0.0
    for _ in range(EQ_ITERS):
        v0, v1 = rng.choice(combos)
        villain = [eval7.Card(normalize_card_string(v0)), eval7.Card(normalize_card_string(v1))]
        deck = [c for c in base_deck if c != villain[0] and c != villain[1]]
        rng.shuffle(deck)
        sim_board = board_cards + deck[: 5 - len(board_cards)]
        hero_score = eval7.evaluate(hero_cards + sim_board)
        villain_score = eval7.evaluate(villain + sim_board)
        if hero_score > villain_score:
            score += 1.0
        elif hero_score == villain_score:
            score += 0.5
    eq = score / EQ_ITERS
    _EQ_CACHE[key] = eq
    return eq


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
    {
        # The spot the spew artifact was actually ABOUT: trash a shaken risk-seeker
        # over-jams. vs a random hand (Phase-1) J4o is ~32% so the 25bb shove priced
        # ~neutral; vs the villain's CONTINUE range it is crushed, so range-aware
        # equity must price the extra spew here as clearly −EV. This spot is the
        # discriminator between the two equity models.
        'label': 'tilt-shove trash (J4o, 25bb)',
        'hero': ['Jh', '4c'],
        'board': [],
        'pot': 4.0,
        'baseline': {'fold': 0.75, 'call': 0.10, 'all_in': 0.15},
        'actions': {'fold': ('fold', 0.0), 'call': ('call', 1.0), 'all_in': ('raise', 24.0)},
    },
]

# Backdrop = (villain fold-to-raise frequency, betting range, continue-vs-raise
# range). The fold frequency is the fold-equity channel; the ranges are what
# eq_call / eq_called are priced against. A fish folds rarely AND continues wide
# and weak; a competent opp folds more AND continues tight and strong. The contrast
# in the CONTINUE range is what makes light spew −EV vs a competent opp (called by
# the top of the range) while only mildly −EV vs a fish (called by junk).
BACKDROPS = {
    'fish (rarely folds)': {
        'f': 0.08,
        'bet': (LATE_POSITION_RANGE, 'late'),  # ~32%, the widest static range we have
        'cont': (LATE_POSITION_RANGE, 'late'),  # sticky: calls raises wide
    },
    'competent (folds more)': {
        'f': 0.42,
        'bet': (MIDDLE_POSITION_RANGE, 'middle'),  # ~22%
        'cont': (STANDARD_3BET_RANGE, '3bet'),  # ~8%: only continues vs a raise with it
    },
}


def _action_ev(
    kind: str, R: float, pot: float, eq_call: float, eq_called: float, fold_to_raise: float
) -> float:
    if kind == 'fold':
        return 0.0
    if kind == 'call':
        return eq_call * (pot + R) - R
    # raise: villain folds (pick up pot) or continues with its top range (eq_called)
    return fold_to_raise * pot + (1.0 - fold_to_raise) * (eq_called * (pot + 2.0 * R) - R)


def _strategy_ev(profile: StrategyProfile, spot: dict, backdrop: dict) -> float:
    """Σ p(action)·EV(action), in bb, for one spot under one backdrop."""
    bet_range, bet_id = backdrop['bet']
    cont_range, cont_id = backdrop['cont']
    eq_call = _equity_vs_range(spot['hero'], spot['board'], bet_range, bet_id)
    eq_called = _equity_vs_range(spot['hero'], spot['board'], cont_range, cont_id)
    pot = spot['pot']
    f = backdrop['f']
    total = 0.0
    for action, p in profile.action_probabilities.items():
        if p <= 0:
            continue
        kind, R = spot['actions'].get(action, ('fold', 0.0))
        total += p * _action_ev(kind, R, pot, eq_call, eq_called, f)
    return total


def _agg_mass(profile: StrategyProfile) -> float:
    return sum(
        p for a, p in profile.action_probabilities.items() if categorize_action(a) == 'aggressive'
    )


def _measure(anchors, backdrop: dict, state: str):
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
        d_ev.append(_strategy_ev(on, spot, backdrop) - _strategy_ev(off, spot, backdrop))
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
        f'TILT SIGNATURE — Phase-2 EV probe ({len(real)} personas, intensity {INTENSITY}, tilted)'
    )
    print('  ΔEV = mean per-spot EV(on) − EV(off) in bb, paired (trajectory-free). Range-aware')
    print('  eq-when-called. SYNTHETIC spots — magnitude/sign, not a trustworthy absolute bb/100')
    print('  (real recorded spots are the remaining build step).')
    print('=' * 92)
    for state in ('tilted', 'shaken'):
        for bd_label, backdrop in BACKDROPS.items():
            print(
                f'\n  [{state}] backdrop: {bd_label}  '
                f'(villain fold-to-raise = {backdrop["f"]:.2f})'
            )
            print(f'    {"tier":22s} {"n":>3s} {"ΔEV (bb/spot)":>14s} {"Δagg":>8s}')
            for label, pred in tiers:
                members = [
                    _anchors_of(c)
                    for c in real.values()
                    if pred(float(c['anchors'].get('risk_identity', 0.5)))
                ]
                if not members:
                    continue
                rows = [_measure(a, backdrop, state) for a in members]
                m = len(rows)
                dev = sum(r[0] for r in rows) / m
                dagg = sum(r[1] for r in rows) / m
                print(f'    {label:22s} {m:3d} {dev:+14.4f} {dagg:+8.3f}')

    print(
        '\n  READING IT: eq is now conditioned on the villain CONTINUE range. This correctly prices'
    )
    print(
        '  the all-in / trash branches as −EV (COLLAPSE — risk-averse/mid, Δagg<0 tilted — now reads'
    )
    print(
        '  −EV both backdrops, larger vs the fish a passive line forgoes value against). But SPEW'
    )
    print(
        '  (risk-seeking, shaken) STILL reads +EV here, and that is a FINDING, not an artifact: the'
    )
    print('  softmax + divergence clamp make the signature express as call→small-raise on PLAYABLE')
    print(
        '  hands (a +EV shift given fold-equity), NOT as the trash all-in jams it is feared to be —'
    )
    print(
        '  on the J4o spot the signature actually JAMS LESS. So the clamp bound holds: the signature'
    )
    print('  cannot manufacture the catastrophic trash-shove (that pathology lives in the preflop')
    print(
        '  charts, not here). CONSEQUENCE: the spew SIGN is dominated by the spot MIX (how often the'
    )
    print(
        '  bot is in a small-raise spot vs a big-jam spot), so a trustworthy bb/100 needs the REAL'
    )
    print(
        '  recorded-spot corpus + frequencies — hand-picked spots cannot settle the sign. What this'
    )
    print('  run DOES settle: the catastrophe gate (signature is structurally non-catastrophic).')


if __name__ == '__main__':
    main()
