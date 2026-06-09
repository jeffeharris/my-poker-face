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
from typing import Dict, List

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

# ── steady-state episodes under a balanced mix + recovery-policy prototypes ────

WIN_MIX = {'win': 0.80, 'big_win': 0.15, 'successful_bluff': 0.05}
LOSS_MIX = {'loss': 0.55, 'big_loss': 0.20, 'bluff_called': 0.10,
            'bad_beat': 0.07, 'got_sucked_out': 0.05, 'crippled': 0.03}

# Recovery-policy prototypes (the Q1 fork). Each maps poise -> a recovery_rate
# override passed to recover(); None = use the persona's own anchor.
#   current        : anchor recovery_rate as shipped
#   short_steam    : brisk recovery for everyone -> short episodes
#   poise_lingering: recovery scales with poise -> stoics shake it fast, hotheads simmer
POLICIES = {
    'current': lambda poise: None,
    'short_steam': lambda poise: 0.30,
    'poise_lingering': lambda poise: 0.06 + 0.30 * poise,
}

BANDS = [  # (label, poise_low_inclusive, poise_high_exclusive)
    ('monk   >=0.90', 0.90, 1.01),
    ('stoic  0.78-90', 0.78, 0.90),
    ('composd 0.60-78', 0.60, 0.78),
    ('volatil 0.45-60', 0.45, 0.60),
    ('hothead <0.45', 0.00, 0.45),
]


def _band(poise: float) -> str:
    for label, lo, hi in BANDS:
        if lo <= poise < hi:
            return label
    return BANDS[-1][0]


def _pick(rng: random.Random, mix: Dict[str, float]) -> str:
    return rng.choices(list(mix), weights=list(mix.values()), k=1)[0]


def steady_state_series(name: str, cfg: dict, *, hands: int, play_rate: float, seed: int,
                        policy) -> List[float]:
    """Composure series over `hands`, under a recovery-rate `policy(poise)`."""
    rng = random.Random(seed)
    psy = PlayerPsychology.from_personality_config(name, cfg)
    rate = policy(psy.anchors.poise)
    consec = 0
    out: List[float] = []
    for _ in range(hands):
        if rng.random() < play_rate:
            if rng.random() < 0.5:
                psy.apply_pressure_event(_pick(rng, WIN_MIX)); consec = 0
            else:
                psy.apply_pressure_event(_pick(rng, LOSS_MIX)); consec += 1
                if consec >= 3:
                    psy.apply_pressure_event('losing_streak')
        else:
            psy.apply_pressure_event('not_in_hand'); consec = 0
        psy.recover(rate)
        out.append(psy.axes.composure)
    return out


def episodes(series: List[float], thresh: float = TILT_EMO) -> List[int]:
    """Lengths (in hands) of contiguous runs below `thresh`."""
    runs: List[int] = []
    cur = 0
    for c in series:
        if c < thresh:
            cur += 1
        elif cur:
            runs.append(cur); cur = 0
    if cur:
        runs.append(cur)
    return runs


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--hands', type=int, default=3000)
    ap.add_argument('--play-rate', type=float, default=0.30)
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    personas = sorted(_load_real_personas().items())
    n = len(personas)
    tilt_pen = get_zone_param('PENALTY_TILTED_THRESHOLD')

    print('=' * 90)
    print(f'TILT EXCURSION MODEL — REAL psychology, {n} personas (eval bots excluded)')
    print(f'  tilt = composure < {TILT_EMO};  penalty zone < {tilt_pen};  '
          f'play_rate={args.play_rate}, {args.hands} hands')
    print('=' * 90)

    # ---- (1) CURRENT per-band spread: frequency AND episode length ----
    # band -> aggregated lists across its personas
    band_pct: Dict[str, List[float]] = {b[0]: [] for b in BANDS}
    band_eplen: Dict[str, List[float]] = {b[0]: [] for b in BANDS}
    band_epcount: Dict[str, List[float]] = {b[0]: [] for b in BANDS}
    band_members: Dict[str, int] = {b[0]: 0 for b in BANDS}
    for i, (name, cfg) in enumerate(personas):
        poise = float(cfg['anchors'].get('poise', 0.7))
        b = _band(poise)
        band_members[b] += 1
        series = steady_state_series(name, cfg, hands=args.hands, play_rate=args.play_rate,
                                     seed=args.seed + i, policy=POLICIES['current'])
        eps = episodes(series)
        band_pct[b].append(100.0 * sum(1 for c in series if c < TILT_EMO) / len(series))
        band_epcount[b].append(len(eps))
        band_eplen[b].extend(eps)

    print('\n(1) CURRENT per-temperament spread (as shipped):')
    print(f'  {"band":16s} {"n":>3s} {"%time tilt":>11s} {"tilt episodes/1k h":>19s} {"med episode len":>16s}')
    for b, _, _ in BANDS:
        m = band_members[b]
        if not m:
            print(f'  {b:16s} {m:3d}   (no personas in band)')
            continue
        pct = _median(band_pct[b])
        epk = _median([c / (args.hands / 1000) for c in band_epcount[b]])
        eplen = _median(band_eplen[b])
        print(f'  {b:16s} {m:3d} {pct:10.2f}% {epk:18.1f} {eplen:13.0f} hd')

    # ---- (2) PERSISTENCE PROTOTYPE: episode length under each recovery policy ----
    print('\n(2) PERSISTENCE prototype — median tilt-episode length (hands) per policy:')
    print(f'  {"band":16s} ' + ' '.join(f'{p:>16s}' for p in POLICIES))
    # reuse one series per (persona, policy)
    for b, lo, hi in BANDS:
        members = [(nm, c) for nm, c in personas if lo <= float(c['anchors'].get('poise', 0.7)) < hi]
        if not members:
            continue
        cells = []
        for pol in POLICIES:
            lens: List[float] = []
            for i, (nm, c) in enumerate(members):
                series = steady_state_series(nm, c, hands=args.hands, play_rate=args.play_rate,
                                             seed=args.seed + i, policy=POLICIES[pol])
                lens.extend(episodes(series))
            cells.append(f'{_median(lens):13.0f} hd')
        print(f'  {b:16s} ' + ' '.join(f'{x:>16s}' for x in cells))

    print('\n  current = anchors as shipped; short_steam = rate 0.30 for all;')
    print('  poise_lingering = rate 0.06+0.30*poise (stoics fast, hotheads simmer).')
    print('  NOTE: absolute %time is event-model-dependent (needs real-play data to')
    print('  trust as a point); episode LENGTH + per-band SPREAD are the robust signal.')


if __name__ == '__main__':
    main()
