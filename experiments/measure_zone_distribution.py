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

# Recovery policies. Each is a per-hand fn(psy) -> recovery_rate override for the
# upcoming recover() call (None = use the persona's anchor rate). Evaluated each
# hand so a policy can be composure-aware (the drag below).
#   current        : anchor recovery_rate as shipped
#   short_steam    : brisk recovery for everyone -> short episodes
#   poise_lingering: recovery scales with poise (composure-blind)
POLICIES = {
    'current': lambda psy: None,
    'short_steam': lambda psy: 0.30,
    'poise_lingering': lambda psy: 0.06 + 0.30 * psy.anchors.poise,
}


def make_drag(floor: float, exp: float = 1.0):
    """TILT_EXCURSION_DESIGN.md slow-recovery-while-tilted: WHILE composure is
    below the tilt line, scale the anchor recovery rate by a poise-scaled drag
    `floor + (1-floor)*poise**exp` (in (0,1] -> slower climb-out, episode lasts
    longer); above the line, normal anchor recovery. Lower floor / higher exp =>
    bigger stoic-vs-hothead episode-length spread. Never latches (drag>0, and a
    win lifts composure directly), so the never-chronic invariant holds."""
    def rate_fn(psy):
        if psy.axes.composure < TILT_EMO:
            poise = psy.anchors.poise
            drag = floor + (1.0 - floor) * (poise ** exp)
            return psy.anchors.recovery_rate * drag
        return None
    return rate_fn

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
    """Composure series over `hands`. `policy` is a per-hand fn(psy)->rate|None."""
    rng = random.Random(seed)
    psy = PlayerPsychology.from_personality_config(name, cfg)
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
        psy.recover(policy(psy))
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


def _pctile(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


# Per-band target median tilt-episode length (hands) from TILT_EXCURSION_DESIGN.md §2.
TARGET_EPLEN = {
    'monk   >=0.90': (0, 0),
    'stoic  0.78-90': (2, 4),
    'composd 0.60-78': (4, 7),
    'volatil 0.45-60': (6, 10),
    'hothead <0.45': (12, 20),
}


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

    # ---- (2) FIT the slow-recovery-while-tilted drag to the episode-len targets ----
    members_by_band = {
        b: [(nm, c) for nm, c in personas if lo <= float(c['anchors'].get('poise', 0.7)) < hi]
        for b, lo, hi in BANDS
    }
    target_bands = [b for b, _, _ in BANDS if b != 'monk   >=0.90']

    def eval_config(floor: float, exp: float):
        """Return {band: (median_eplen, pct_time_tilt, p95_eplen)} for a drag config."""
        pol = make_drag(floor, exp)
        res = {}
        for b in target_bands:
            lens: List[float] = []
            pcts: List[float] = []
            for i, (nm, c) in enumerate(members_by_band[b]):
                series = steady_state_series(nm, c, hands=args.hands, play_rate=args.play_rate,
                                             seed=args.seed + i, policy=pol)
                eps = episodes(series)
                lens.extend(eps)
                pcts.append(100.0 * sum(1 for x in series if x < TILT_EMO) / len(series))
            res[b] = (_median(lens), _median(pcts), _pctile(lens, 0.95))
        return res

    print('\n(2) FIT slow-recovery-while-tilted — median episode length (hd) per (floor, exp):')
    print('    target medians: stoic 2-4, composed 4-7, volatile 6-10, hothead 12-20')
    hdr = '  '.join(f'{b.split()[0]:>8s}' for b in target_bands)
    print(f'  {"floor":>5s} {"exp":>4s}   {hdr}   {"hits":>4s} {"hot95p":>6s}')
    grid = [(f, e) for e in (1.0, 2.0) for f in (0.20, 0.10, 0.05, 0.0)]
    best = None
    for floor, exp in grid:
        res = eval_config(floor, exp)
        hits = sum(1 for b in target_bands
                   if TARGET_EPLEN[b][0] <= res[b][0] <= TARGET_EPLEN[b][1])
        hot95 = res['hothead <0.45'][2]
        cells = '  '.join(f'{res[b][0]:8.0f}' for b in target_bands)
        flag = ' chronic?' if hot95 > 30 else ''
        print(f'  {floor:5.2f} {exp:4.1f}   {cells}   {hits:>2d}/4 {hot95:5.0f}{flag}')
        score = (hits, -hot95)
        if best is None or score > best[0]:
            best = (score, floor, exp, res)

    assert best is not None
    _, bf, be, bres = best
    print(f'\n  BEST FIT: floor={bf}, exp={be}  (TILT_DRAG_FLOOR={bf}, drag=floor+(1-floor)*poise**{be})')
    print(f'  {"band":16s} {"med eplen":>9s} {"target":>9s} {"%time":>7s} {"95p len":>8s}')
    for b in target_bands:
        med, pct, p95 = bres[b]
        lo, hi = TARGET_EPLEN[b]
        ok = '✓' if lo <= med <= hi else '✗'
        print(f'  {b:16s} {med:7.0f}hd {f"{lo}-{hi}":>9s} {pct:6.2f}% {p95:6.0f}hd {ok}')
    print('\n  never-chronic check: hothead 95th-pctile episode length stays bounded (≤~30).')
    print('  NOTE: absolute %time is event-model-dependent; episode LENGTH + spread are robust.')


if __name__ == '__main__':
    main()
