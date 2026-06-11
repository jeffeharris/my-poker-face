"""Corpus-mode bb/100 for the §4 tilt SIGNATURE — the trustworthy build
(docs/plans/TILT_EV_HARNESS.md, approach C, parts (a)+(b) combined).

This is `tilt_ev_probe.py` with its hand-authored synthetic `SPOTS` replaced by the
REAL recorded corpus (`experiments/tilt_corpus_extract.py` → `tilt_corpus.jsonl`):
the spots the bot actually reached while in a tilt state, with their real geometry,
baseline (pre-emotion) strategy, anchors, deviation profile, and emotional_state.

For each recorded spot it re-runs the REAL `modify_strategy` pipeline twice —
TILT_SIGNATURE_ENABLED off then on, on the identical spot — and prices both action
distributions in bb with the SAME range-aware EV model as the synthetic probe
(`_equity_vs_range` reused), against both a fish and a competent backdrop. ΔEV =
EV(on) − EV(off) per spot. The headline:

    bb/100hands = 100 · (Σ_spots ΔEV) / num_hands           (from corpus.meta.json)

i.e. the per-spot ΔEV summed over every tilted decision and amortized across all
hands played — so the tilted-decision FREQUENCY and the real spot MIX (the two
things the synthetic probe could not supply) are now both baked in. Reported overall
and per persona (the hothead — Fyodor here — is the one that matters).

EV MODEL (heads-up, bb, identical to the synthetic probe):
  fold/check : 0  /  eq_call·pot           (check = free showdown: call at cost 0)
  call       : eq_call·(pot + cost) − cost
  raise/bet  : f·pot + (1 − f)·(eq_called·(pot + 2R) − R)
Action labels → (kind, R_bb) via `_action_kind_R`, mirroring action_mapper sizing
(raise_Nbb → to N bb; raise_Nx → N×highest_bet; bet_p/raise_p → pot-fraction;
jam/all_in → effective stack).

Run: docker compose exec -T backend python3 -m experiments.tilt_corpus_ev \
        [--corpus experiments/data/tilt_corpus.jsonl]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import experiments.tilt_ev_probe as _tep
from experiments.tilt_ev_probe import BACKDROPS, _equity_vs_range
from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import categorize_action, modify_strategy
from poker.strategy.strategy_profile import StrategyProfile

# The corpus has ~unique hero/board per spot, so the equity cache rarely hits;
# ±1% MC precision is ample for a bb/100 estimate, so trade iterations for speed.
_tep.EQ_ITERS = 6000

FLAG = 'TILT_SIGNATURE_ENABLED'
PROJECT_ROOT = Path(__file__).parent.parent
_MIN_INCR_BB = 1.0  # floor on a raise increment so degenerate sizings don't price as ~0

_RAISE_RE = re.compile(r'^(raise|bet)_(\d+(?:\.\d+)?)(bb|x)?$')


def _action_kind_R(action: str, geo: dict) -> tuple[str, float]:
    """Map a recorded action label to (kind, R_bb) under this spot's geometry.

    kind ∈ {fold, call, raise}; R_bb is the call cost (call) or the hero's added
    chips (raise), both in big blinds. Mirrors poker/strategy/action_mapper.py.
    """
    pot_bb = geo['pot_bb']
    cost_bb = geo['cost_bb']
    player_bet_bb = geo['player_bet_bb']
    highest_bet_bb = player_bet_bb + cost_bb
    eff_stack_bb = geo['eff_stack_bb']

    if action == 'fold':
        return ('fold', 0.0)
    if action == 'check':
        return ('call', 0.0)  # free showdown: realize equity in the current pot
    if action == 'call':
        return ('call', cost_bb)
    if action in ('jam', 'all_in'):
        return ('raise', max(_MIN_INCR_BB, eff_stack_bb - player_bet_bb))

    m = _RAISE_RE.match(action)
    if m:
        kind_tok, num_s, suffix = m.group(1), m.group(2), m.group(3)
        num = float(num_s)
        if suffix == 'bb':  # preflop: raise TO num big blinds
            raise_to_bb = num
            R = raise_to_bb - player_bet_bb
        elif suffix == 'x':  # preflop: raise TO num × the current highest bet
            raise_to_bb = num * max(highest_bet_bb, 1.0)
            R = raise_to_bb - player_bet_bb
        elif kind_tok == 'bet':  # postflop bet: num% of pot, added on top
            R = (num / 100.0) * pot_bb
        else:  # postflop raise_<pct>: pct of pot-after-call, over the highest bet
            target = (num / 100.0) * (pot_bb + cost_bb)
            R = cost_bb + target
        R = max(_MIN_INCR_BB, min(R, eff_stack_bb - player_bet_bb))
        return ('raise', R)

    # Unknown label — treat as passive zero (shouldn't happen with current charts).
    return ('call', 0.0)


def _action_ev(
    kind: str, R: float, pot_bb: float, eq_call: float, eq_called: float, f: float
) -> float:
    if kind == 'fold':
        return 0.0
    if kind == 'call':
        return eq_call * (pot_bb + R) - R
    return f * pot_bb + (1.0 - f) * (eq_called * (pot_bb + 2.0 * R) - R)


def _strategy_ev(profile, geo: dict, backdrop: dict) -> float:
    bet_range, bet_id = backdrop['bet']
    cont_range, cont_id = backdrop['cont']
    eq_call = _equity_vs_range(geo['hero'], geo['board'], bet_range, bet_id)
    eq_called = _equity_vs_range(geo['hero'], geo['board'], cont_range, cont_id)
    f = backdrop['f']
    total = 0.0
    for action, p in profile.action_probabilities.items():
        if p <= 0:
            continue
        kind, R = _action_kind_R(action, geo)
        total += p * _action_ev(kind, R, geo['pot_bb'], eq_call, eq_called, f)
    return total


def _agg_mass(profile) -> float:
    return sum(
        p for a, p in profile.action_probabilities.items() if categorize_action(a) == 'aggressive'
    )


def _geo(spot: dict) -> dict:
    bb = float(spot.get('big_blind') or 100)
    player_bet_bb = float(spot.get('player_bet') or 0) / bb
    eff = spot.get('effective_stack_bb')
    eff_stack_bb = float(eff) if eff else float(spot.get('player_stack') or 0) / bb
    return {
        'bb': bb,
        'hero': spot['hero'],
        'board': spot['board'],
        'pot_bb': float(spot.get('pot_total') or 0) / bb,
        'cost_bb': float(spot.get('cost_to_call') or 0) / bb,
        'player_bet_bb': player_bet_bb,
        'eff_stack_bb': max(eff_stack_bb, player_bet_bb + _MIN_INCR_BB),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default='experiments/data/tilt_corpus.jsonl')
    ap.add_argument(
        '--mode',
        choices=['signature', 'emotional'],
        default='signature',
        help=(
            "signature: ΔEV of TILT_SIGNATURE_ENABLED on vs off (the refinement). "
            "emotional: ΔEV of the always-on emotional shift vs the raw pre-emotion "
            "baseline — the EXP_009 Phase-A 'is it noticeable today' magnitude."
        ),
    )
    args = ap.parse_args()
    corpus_path = PROJECT_ROOT / args.corpus
    meta = json.load(open(corpus_path.with_suffix('.meta.json')))
    spots = [json.loads(line) for line in open(corpus_path)]

    num_hands = meta['num_hands']
    # per backdrop: persona -> [sum ΔEV, count, sum Δagg]
    agg = {bd: defaultdict(lambda: [0.0, 0, 0.0]) for bd in BACKDROPS}
    skipped = 0

    for spot in spots:
        hero = spot.get('hero') or []
        if len(hero) != 2:  # need hole cards to price equity
            skipped += 1
            continue
        base = spot['base_strategy_probs']
        legal = spot.get('legal_actions') or list(base.keys())
        try:
            anchors = PersonalityAnchors.from_dict(spot['anchors'])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        es_d = spot['emotional_state']
        es = SimpleNamespace(
            state=es_d['state'],
            intensity=es_d['intensity'],
            severity=es_d.get('severity', 'moderate'),
        )
        profile = DEVIATION_PROFILES.get(
            spot.get('deviation_profile_name', 'tag'), DEVIATION_PROFILES['tag']
        )
        base_profile = StrategyProfile(action_probabilities=dict(base))

        if args.mode == 'emotional':
            # Phase-A baseline: always-on emotional shift vs the raw pre-emotion
            # baseline. `off` = the unmodified chart strategy (no emotion); `on` =
            # the legacy emotional shift (signature flag OFF). ΔEV = the emotional
            # impact the bot already applies today, before any tilt-excursion flag.
            off = base_profile
            os.environ[FLAG] = '0'
            on, _ = modify_strategy(base_profile, legal, anchors, es, profile)
        else:
            os.environ[FLAG] = '0'
            off, _ = modify_strategy(base_profile, legal, anchors, es, profile)
            os.environ[FLAG] = '1'
            on, _ = modify_strategy(base_profile, legal, anchors, es, profile)

        geo = _geo(spot)
        dagg = _agg_mass(on) - _agg_mass(off)
        for bd_label, backdrop in BACKDROPS.items():
            dev = _strategy_ev(on, geo, backdrop) - _strategy_ev(off, geo, backdrop)
            for key in (spot['persona'], '__ALL__'):
                row = agg[bd_label][key]
                row[0] += dev
                row[1] += 1
                row[2] += dagg

    title = 'EMOTIONAL SHIFT (vs raw baseline)' if args.mode == 'emotional' else 'TILT SIGNATURE'
    print('=' * 96)
    print(
        f'{title} — corpus bb/100  (exp {meta["experiment_id"]}, {len(spots)} tilted spots, '
        f'{num_hands} hands)'
    )
    print(
        f'  tilted-decision rate {meta["tilted_decision_rate_pct"]:.2f}%  |  states {meta["per_state"]}'
    )
    print('  bb/100hands = 100·ΣΔEV / hands. Real recorded spots + range-aware eq. ΔEV = on − off.')
    print('=' * 96)
    for bd_label in BACKDROPS:
        print(f'\n  backdrop: {bd_label}  (villain fold-to-raise = {BACKDROPS[bd_label]["f"]:.2f})')
        print(
            f'    {"persona":22s} {"spots":>6s} {"ΣΔEV(bb)":>10s} {"bb/100":>9s} {"mean Δagg":>10s}'
        )
        rows = agg[bd_label]
        for key in sorted(rows, key=lambda k: (k != '__ALL__', k)):
            s_dev, n, s_agg = rows[key]
            if not n:
                continue
            bb100 = 100.0 * s_dev / num_hands
            label = 'ALL' if key == '__ALL__' else key
            print(f'    {label:22s} {n:6d} {s_dev:+10.3f} {bb100:+9.3f} {s_agg / n:+10.3f}')
    if skipped:
        print(f'\n  ({skipped} spots skipped — no hole cards or unparseable anchors)')
    print(
        '\n  READING IT: bb/100hands is the signature\'s cost/gain amortized across ALL hands, with'
    )
    print('  the real tilted-decision frequency + spot mix baked in. Compare fish vs competent — a')
    print('  cost that only appears vs the fish is overfit. Per-persona ALL is dominated by the')
    print('  hothead (most tilted spots). This is the number TILT_EV_HARNESS scoped as the gate.')


if __name__ == '__main__':
    main()
