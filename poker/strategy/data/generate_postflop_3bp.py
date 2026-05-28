"""Generate 3-bet-pot (3BP) postflop entries from the single-raised-pot (SRP) chart.

The postflop chart was authored only at `pot_type='SRP'`, and the classifier
hardcoded SRP, so 3-bet pots played single-raised-pot strategy (see
`docs/plans/CHART_COVERAGE_AND_GENERATION.md`). This fills the `pot_type`
dimension: `postflop_strategies_3bp.json`, derived from the SRP entries
(authored high + generated medium/low), merged at load.

What's genuinely different in a 3-bet pot (beyond SPR, which is a separate
dimension already handled): ranges are **condensed and stronger** and the pot
is more **polarized**. So relative to SRP at the same SPR:
- value (made value, or strong draw) bets/raises a bit MORE (stronger ranges
  want stacks in) — shift some passive mass into aggression;
- air/weak bluffs a bit LESS (condensed ranges = less fold equity) — shift some
  bluff mass to give-up.

These are deliberately MILD (the node has no explicit aggressor flag, and the
SPR dimension already captures the lower-SPR-ness of 3-bet pots). v0 / coarse;
validated by measure_passivity. Re-run:
    docker compose exec backend python -m poker.strategy.data.generate_postflop_3bp
"""

from __future__ import annotations

import json
import os
from typing import Dict

_COMMIT_MADE = frozenset({'nuts', 'strong_made', 'medium_made'})
_COMMIT_DRAW = frozenset({'strong_draw'})

# Fraction of passive (check/call) mass that value hands convert to aggression
# in a 3-bet pot; fraction of bluff (bet/raise) mass that air gives up. Mild.
VALUE_PUSH = 0.20
BLUFF_CUT = 0.25

# Default aggressive label to grow when the SRP entry has no aggressive mass to
# scale (value hand that purely checks/calls at SRP but should bet more in 3BP).
_DEFAULT_BET = 'bet_67'
_DEFAULT_RAISE = 'raise_67'


def _is_aggressive(action: str) -> bool:
    return action.startswith(('bet_', 'raise_'))


def _norm(profile: Dict[str, float]) -> Dict[str, float]:
    clean = {a: p for a, p in profile.items() if p > 1e-9}
    total = sum(clean.values())
    if total <= 0:
        return {'check': 1.0}
    out = {a: round(p / total, 3) for a, p in clean.items()}
    drift = round(1.0 - sum(out.values()), 3)
    if abs(drift) >= 0.001:
        top = max(out, key=out.get)
        out[top] = round(out[top] + drift, 3)
    return out


def transform_3bp(actions: Dict[str, float], made: str, draw: str, facing: str) -> Dict[str, float]:
    """Polarize an SRP action distribution toward 3-bet-pot dynamics."""
    new = dict(actions)
    commit_worthy = made in _COMMIT_MADE or draw in _COMMIT_DRAW
    aggressive = [a for a in new if _is_aggressive(a)]

    if commit_worthy:
        # Value bets/raises more: pull from the passive action into aggression.
        passive_key = 'check' if facing == 'unopened' else 'call'
        move = VALUE_PUSH * new.get(passive_key, 0.0)
        if move > 0:
            new[passive_key] = new.get(passive_key, 0.0) - move
            if aggressive:
                agg_total = sum(new[a] for a in aggressive)
                for a in aggressive:  # scale existing sizing up
                    new[a] += move * (new[a] / agg_total)
            else:  # no sizing yet → default
                sink = _DEFAULT_BET if facing == 'unopened' else _DEFAULT_RAISE
                new[sink] = new.get(sink, 0.0) + move
    else:
        # Air/weak bluffs less: give up part of the bluff mass.
        bluff_mass = sum(new[a] for a in aggressive)
        if bluff_mass > 0:
            give = BLUFF_CUT * bluff_mass
            for a in aggressive:
                new[a] -= give * (new[a] / bluff_mass)
            sink = 'check' if facing == 'unopened' else 'fold'
            new[sink] = new.get(sink, 0.0) + give
    return _norm(new)


def _load_all_srp(here: str) -> Dict[str, Dict[str, float]]:
    """Every SRP entry: authored high + generated low (medium uses the high
    strategy via the SPR fallback — committing at medium SPR measurably
    regresses, so no distinct medium slice exists)."""
    srp: Dict[str, Dict[str, float]] = {}
    with open(os.path.join(here, 'postflop_strategies.json')) as f:
        srp.update(json.load(f))
    low_path = os.path.join(here, 'postflop_strategies_low_spr.json')
    if os.path.exists(low_path):
        with open(low_path) as f:
            srp.update(json.load(f))
    return {k: v for k, v in srp.items() if k.split('|')[2] == 'SRP'}


def build_3bp_chart(srp: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for key, actions in srp.items():
        parts = key.split('|')
        if len(parts) < 8 or parts[2] != 'SRP':
            continue
        street, pos, _pot, texture, made, draw, facing, spr = parts[:8]
        new_key = '|'.join([street, pos, '3BP', texture, made, draw, facing, spr])
        out[new_key] = transform_3bp(actions, made, draw, facing)
    return out


def main() -> None:
    here = os.path.dirname(__file__)
    srp = _load_all_srp(here)
    chart = build_3bp_chart(srp)
    path = os.path.join(here, 'postflop_strategies_3bp.json')
    with open(path, 'w') as f:
        json.dump(chart, f, indent=2, sort_keys=True)
        f.write('\n')
    print(f"wrote {path} ({len(chart)} 3BP entries from {len(srp)} SRP entries)")


if __name__ == '__main__':
    main()
