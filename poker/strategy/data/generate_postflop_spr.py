"""Generate real low-SPR postflop entries from the high-SPR chart.

`postflop_strategies.json` was authored only at `spr_bucket='high'` (see
`docs/plans/CHART_COVERAGE_AND_GENERATION.md`). At low SPR (< 2) the lookup
falls back to that high-SPR entry — which has the wrong *sizing* (bets 33/67%
pot where a committed stack should jam) and the wrong *bluff frequency* (it
floats/bluffs as if there were streets left to apply pressure). This script
derives a real low-SPR slice, `postflop_strategies_low_spr.json`, by a
hand-authored transform of the high-SPR entries. Merged at load time; the
authored high-SPR file stays pristine.

Governing principle at low SPR: **commit or give up — there is no small bet.**
- Commit-worthy hands (made value, or a strong draw with fold equity + outs):
  collapse all bet/raise mass into `jam` (any bet commits anyway; jamming gets
  max value and denies later fold-out).
- Everything else (air / weak with no strong draw): stop bluffing — there's no
  fold equity to lever and you're not pricing in a draw. Bet → check (give up),
  bluff-raise → fold. Keep existing check/call/fold.

This intentionally leaves `check`/`call` frequencies from the high-SPR chart in
place; the postflop_commit layer (value → jam) and math_floor still run on top.

Re-run after edits:
    docker compose exec backend python -m poker.strategy.data.generate_postflop_spr

Validate: experiments/measure_passivity.py --stack-bb 25/50 --opponents jeff/gto/mix
"""

from __future__ import annotations

import json
import os
from typing import Dict

# Made-hand tiers worth committing at low SPR. medium_made is included: one
# pair in a sub-2 SPR pot is a stack-off, not a pot-control spot.
_COMMIT_MADE = frozenset({'nuts', 'strong_made', 'medium_made'})
# A strong draw commits regardless of made tier (semi-bluff jam: fold equity
# + direct outs). weak_draw / backdoor do not.
_COMMIT_DRAW = frozenset({'strong_draw'})


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


def transform_low_spr(
    actions: Dict[str, float], made: str, draw: str, facing: str
) -> Dict[str, float]:
    """Map a high-SPR action distribution to its low-SPR counterpart."""
    aggressive_mass = sum(p for a, p in actions.items() if _is_aggressive(a))
    passive = {a: p for a, p in actions.items() if not _is_aggressive(a)}
    if aggressive_mass <= 0:
        return _norm(passive)  # nothing to re-route (pure check/call/fold)

    commit = made in _COMMIT_MADE or draw in _COMMIT_DRAW
    new = dict(passive)
    if commit:
        # Any bet/raise commits at low SPR → jam.
        new['jam'] = new.get('jam', 0.0) + aggressive_mass
    elif facing == 'unopened':
        # No fold equity to bluff into when committed — give up.
        new['check'] = new.get('check', 0.0) + aggressive_mass
    else:
        # Bluff-raise facing a bet/raise: fold instead.
        new['fold'] = new.get('fold', 0.0) + aggressive_mass
    return _norm(new)


def build_low_spr_chart(high: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for key, actions in high.items():
        parts = key.split('|')
        if len(parts) < 8 or parts[7] != 'high':
            continue
        street, pos, pottype, texture, made, draw, facing, _spr = parts[:8]
        low_key = '|'.join([street, pos, pottype, texture, made, draw, facing, 'low'])
        out[low_key] = transform_low_spr(actions, made, draw, facing)
    return out


def main() -> None:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, 'postflop_strategies.json')) as f:
        high = json.load(f)
    low = build_low_spr_chart(high)
    path = os.path.join(here, 'postflop_strategies_low_spr.json')
    with open(path, 'w') as f:
        json.dump(low, f, indent=2, sort_keys=True)
        f.write('\n')
    print(f"wrote {path} ({len(low)} low-SPR entries from {len(high)} high-SPR)")


if __name__ == '__main__':
    main()
