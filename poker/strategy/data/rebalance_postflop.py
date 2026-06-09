"""Rebalance the authored postflop chart for realistic 6-max aggression.

The authored chart (`postflop_strategies.authored.json`, SRP/high-SPR) was too
passive both ways. Measured off the file: unopened-flop c-bet averaged 39.7%
(even nuts bet only 59%), and facing a flop bet, medium_made called 71% / folded
13%. Under the 6-max mixed-field probe this produced **WTSD too high** (calls
down too light) and **c-bet too low** (aggressor checks most flops). See
`docs/plans/ARCHETYPE_POSTFLOP_TUNING.md`.

This derives the LIVE `postflop_strategies.json` from the pristine
`postflop_strategies.authored.json` by two mass shifts:

* **unopened (c-bet)** — move a fraction of CHECK mass into betting, scaled by
  made tier and boosted when holding a draw (semi-bluff). Raises continuation
  betting. NOTE: the node has no aggressor flag, so this also lifts donk-betting
  for a caller first to act — kept moderate for that reason (the clean fix is an
  aggressor-aware node, deferred; see the plan, Lever 2).
* **facing_bet / facing_raise (calling discipline)** — move a fraction of CALL
  mass into FOLD, scaled by made tier, DAMPENED when holding a draw (don't fold
  draws). Facing a raise folds harder than facing a bet. This is the WTSD lever.

Aggressive mass (bet/raise/jam) on facing nodes is preserved; only call↔fold and
check→bet move. Idempotent — always derives from the .authored.json source.

Re-run:
    docker compose run --rm --no-deps --entrypoint python backend \
        -m poker.strategy.data.rebalance_postflop
Validate: scripts/archetype_mixedfield_probe.py (WTSD toward band, c-bet up).
"""

from __future__ import annotations

import json
import os
from typing import Dict

# --- Tunable parameters (iterate against the 6-max probe) ---------------------

# unopened: fraction of CHECK mass moved into betting, by made tier.
CBET_CHECK_TO_BET = {
    'nuts': 0.75,
    'strong_made': 0.62,
    'medium_made': 0.52,
    'weak_made': 0.28,
    'air': 0.33,
}
# Additive boost to the above when the hand also holds a draw (semi-bluff).
CBET_DRAW_BONUS = {'strong_draw': 0.18, 'weak_draw': 0.08, 'backdoor': 0.04, 'no_draw': 0.0}

# facing_bet: fraction of CALL mass moved into FOLD, by made tier. Eased ~12%
# from the first pass — that over-folded the middle of the field (tag/lag/maniac
# WTSD dipped just below band); the per-archetype spread is carried by the
# deviation profiles (station `sticky`, nit folds via ego), not the base chart.
FOLD_FACING_BET = {
    'nuts': 0.0,
    'strong_made': 0.10,
    'medium_made': 0.40,
    'weak_made': 0.36,
    'air': 0.28,
}
# facing_raise folds harder (a raise rep is stronger than a bet).
FOLD_FACING_RAISE = {
    'nuts': 0.0,
    'strong_made': 0.18,
    'medium_made': 0.50,
    'weak_made': 0.45,
    'air': 0.36,
}
# Multiplier on the fold shift when holding a draw — keep draws in.
FOLD_DRAW_DAMPEN = {'strong_draw': 0.30, 'weak_draw': 0.55, 'backdoor': 0.80, 'no_draw': 1.0}

_BET_ACTIONS = ('bet_33', 'bet_67', 'bet_100')


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


def _shift_check_to_bet(actions: Dict[str, float], frac: float) -> Dict[str, float]:
    """Move `frac` of the check mass into betting, keeping the existing bet-size
    mix (or seeding bet_67 if the entry currently never bets)."""
    check = actions.get('check', 0.0)
    if check <= 0 or frac <= 0:
        return dict(actions)
    move = check * min(frac, 1.0)
    new = dict(actions)
    new['check'] = check - move
    bet_total = sum(actions.get(b, 0.0) for b in _BET_ACTIONS)
    if bet_total > 0:
        for b in _BET_ACTIONS:
            if actions.get(b, 0.0) > 0:
                new[b] = new.get(b, 0.0) + move * (actions[b] / bet_total)
    else:
        new['bet_67'] = new.get('bet_67', 0.0) + move
    return new


def _shift_call_to_fold(actions: Dict[str, float], frac: float) -> Dict[str, float]:
    """Move `frac` of the call mass into fold; leave bet/raise/jam untouched."""
    call = actions.get('call', 0.0)
    if call <= 0 or frac <= 0:
        return dict(actions)
    move = call * min(frac, 1.0)
    new = dict(actions)
    new['call'] = call - move
    new['fold'] = new.get('fold', 0.0) + move
    return new


def transform(actions: Dict[str, float], made: str, draw: str, facing: str) -> Dict[str, float]:
    if facing == 'unopened':
        frac = CBET_CHECK_TO_BET.get(made, 0.0) + CBET_DRAW_BONUS.get(draw, 0.0)
        return _norm(_shift_check_to_bet(actions, frac))
    if facing == 'facing_bet':
        frac = FOLD_FACING_BET.get(made, 0.0) * FOLD_DRAW_DAMPEN.get(draw, 1.0)
        return _norm(_shift_call_to_fold(actions, frac))
    if facing == 'facing_raise':
        frac = FOLD_FACING_RAISE.get(made, 0.0) * FOLD_DRAW_DAMPEN.get(draw, 1.0)
        return _norm(_shift_call_to_fold(actions, frac))
    return _norm(actions)


def main() -> None:
    here = os.path.dirname(__file__)
    src = os.path.join(here, 'postflop_strategies.authored.json')
    with open(src) as f:
        authored = json.load(f)
    out = {}
    for key, actions in authored.items():
        parts = key.split('|')
        _street, _pos, _pot, _tex, made, draw, facing, _spr = parts[:8]
        out[key] = transform(actions, made, draw, facing)
    dst = os.path.join(here, 'postflop_strategies.json')
    with open(dst, 'w') as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write('\n')
    print(f'wrote {dst} ({len(out)} entries) from {len(authored)} authored')


if __name__ == '__main__':
    main()
