"""Measure emotional-zone REACHABILITY across the full persona roster, driving the
REAL production psychology (no re-derived formulas).

Why this exists: experiments/psychology_balance_simulator.py re-implemented the
baseline/recovery/sensitivity formulas and ImpactValues and drifted from
production. This harness instead builds a real PlayerPsychology per persona from
personalities.json anchors and calls the real `apply_pressure_event` + `recover`,
so the numbers reflect the SHIPPING system. See
docs/technical/EMOTIONAL_SYSTEM_ANALYSIS.md.

It reports two things, separated by how trustworthy they are:

  (A) RESPONSE-FUNCTION REACHABILITY — robust, event-model-INDEPENDENT.
      For each persona: baseline composure, the drop from a single worst shock
      (bad_beat), hands-to-recover, and the floor + %time-tilted under a sustained
      cooler run (bad_beat/big_loss every played hand). This characterises the
      psychology's RESPONSE and does not depend on guessing how often events fire.

  (B) STEADY-STATE %time-tilted — event-model-DEPENDENT, shown as a sensitivity
      band across a couple of play rates with a balanced (zero-sum-ish) event mix.
      Treat as indicative only: a trustworthy steady-state number needs real-play
      data (prod / a broad game-sim), because the real in-game event frequency is
      not knowable from anchors alone.

Eval bots (recovery_rate == 0, e.g. BaselineSolver/GTO-Lite/CaseBot) are EXCLUDED
— they never recover, so composure decays to 0 and they aren't real personas.

Run:
    docker compose exec -T backend python3 -m experiments.measure_zone_distribution
"""

from __future__ import annotations

import argparse
import json
import random
from typing import Dict, List, Tuple

from poker.player_psychology import PlayerPsychology
from poker.zone_config import get_zone_param

TILT_EMO = 0.40  # emotional_state 'tilted' descriptor — the tilt_conditioning gate


def _load_real_personas() -> Dict[str, dict]:
    """Personas with anchors AND recovery_rate > 0 (excludes static eval bots)."""
    with open('poker/personalities.json') as f:
        data = json.load(f)
    personas = data.get('personalities', data)
    out = {}
    for n, cfg in personas.items():
        if not isinstance(cfg, dict) or 'anchors' not in cfg:
            continue
        if float(cfg['anchors'].get('recovery_rate', 0) or 0) <= 0:
            continue  # static eval bot
        out[n] = cfg
    return out


# ── (A) response-function reachability ───────────────────────────────────────

def reachability(name: str, cfg: dict) -> Dict[str, float]:
    psy = PlayerPsychology.from_personality_config(name, cfg)
    baseline = float(psy._baseline_composure if psy._baseline_composure is not None else psy.axes.composure)

    # single worst shock
    psy.apply_pressure_event('bad_beat')
    after_shock = psy.axes.composure
    # hands to recover back within 0.02 of baseline (no further events)
    hands_to_recover = 0
    for _ in range(60):
        psy.recover()
        hands_to_recover += 1
        if psy.axes.composure >= baseline - 0.02:
            break

    # sustained cooler run: a brutal downswing — bad_beat then big_loss each hand
    psy2 = PlayerPsychology.from_personality_config(name, cfg)
    run_comps: List[float] = []
    for i in range(20):
        psy2.apply_pressure_event('bad_beat' if i % 2 == 0 else 'big_loss')
        psy2.recover()
        run_comps.append(psy2.axes.composure)
    return {
        'poise': psy.anchors.poise,
        'recovery_rate': psy.anchors.recovery_rate,
        'baseline': baseline,
        'shock_drop': baseline - after_shock,
        'shock_floor': after_shock,
        'shock_tilts': 1.0 if after_shock < TILT_EMO else 0.0,
        'recover_hands': float(hands_to_recover),
        'cooler_floor': min(run_comps),
        'cooler_pct_tilt': 100.0 * sum(1 for c in run_comps if c < TILT_EMO) / len(run_comps),
    }


# ── (B) steady-state under a balanced event mix (event-model-dependent) ───────

WIN_MIX = {'win': 0.80, 'big_win': 0.15, 'successful_bluff': 0.05}
LOSS_MIX = {'loss': 0.55, 'big_loss': 0.20, 'bluff_called': 0.10,
            'bad_beat': 0.07, 'got_sucked_out': 0.05, 'crippled': 0.03}


def _pick(rng: random.Random, mix: Dict[str, float]) -> str:
    return rng.choices(list(mix), weights=list(mix.values()), k=1)[0]


def steady_state_pct_tilt(name: str, cfg: dict, *, hands: int, play_rate: float, seed: int) -> float:
    rng = random.Random(seed)
    psy = PlayerPsychology.from_personality_config(name, cfg)
    consec = 0
    tilt = 0
    for _ in range(hands):
        if rng.random() < play_rate:
            if rng.random() < 0.5:  # zero-sum-ish: win or lose ~50/50
                psy.apply_pressure_event(_pick(rng, WIN_MIX)); consec = 0
            else:
                psy.apply_pressure_event(_pick(rng, LOSS_MIX)); consec += 1
                if consec >= 3:
                    psy.apply_pressure_event('losing_streak')
        else:
            psy.apply_pressure_event('not_in_hand'); consec = 0
        psy.recover()
        if psy.axes.composure < TILT_EMO:
            tilt += 1
    return 100.0 * tilt / hands


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--hands', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--top', type=int, default=14)
    args = ap.parse_args()

    personas = _load_real_personas()
    reach: List[Tuple[str, Dict[str, float]]] = [
        (n, reachability(n, cfg)) for n, cfg in sorted(personas.items())
    ]
    n = len(reach)
    tilt_pen = get_zone_param('PENALTY_TILTED_THRESHOLD')

    print('=' * 86)
    print(f'EMOTIONAL REACHABILITY — REAL psychology, {n} personas (eval bots excluded)')
    print(f'  tilt = composure < {TILT_EMO} (emo / tilt_conditioning gate); penalty zone < {tilt_pen}')
    print('=' * 86)

    can_shock_tilt = sum(1 for _, r in reach if r['shock_tilts'])
    can_cooler_tilt = sum(1 for _, r in reach if r['cooler_pct_tilt'] > 0)
    print('\n(A) RESPONSE FUNCTION (robust, event-model-independent):')
    print(f'  personas a SINGLE bad_beat pushes into tilt:        {can_shock_tilt}/{n}')
    print(f'  personas a SUSTAINED cooler run can hold in tilt:   {can_cooler_tilt}/{n}')
    print(f'  median baseline composure: {sorted(r["baseline"] for _, r in reach)[n//2]:.3f}')
    print(f'  median hands-to-recover from one bad_beat: {sorted(r["recover_hands"] for _, r in reach)[n//2]:.0f}')
    print()
    print(f'  {"persona":26s} {"poise":>5s} {"rec":>4s} {"base":>5s} {"1shock→":>7s} {"recovHd":>7s} {"coolerFloor":>11s} {"cooler%tilt":>11s}')
    for name, r in sorted(reach, key=lambda kv: kv[1]['cooler_floor'])[:args.top]:
        print(f'  {name[:26]:26s} {r["poise"]:5.2f} {r["recovery_rate"]:4.2f} {r["baseline"]:5.2f}'
              f' {r["shock_floor"]:7.3f} {r["recover_hands"]:7.0f} {r["cooler_floor"]:11.3f} {r["cooler_pct_tilt"]:10.1f}%')

    print('\n(B) STEADY-STATE %time-tilted (event-model-DEPENDENT — indicative band):')
    for pr in (0.20, 0.35):
        vals = [steady_state_pct_tilt(name, cfg, hands=args.hands, play_rate=pr, seed=args.seed + i)
                for i, (name, cfg) in enumerate(sorted(personas.items()))]
        roster = sum(vals) / n
        worst = max(vals)
        nonzero = sum(1 for v in vals if v > 0)
        print(f'  play_rate={pr:.2f}: roster avg %tilt={roster:5.2f}%  worst persona={worst:5.1f}%  reach-tilt={nonzero}/{n}')
    print('  (steady-state needs real-play data to trust the absolute %; use (A) + relative')
    print('   sweeps for balancing decisions.)')


if __name__ == '__main__':
    main()
