"""Preflop leak drill — turn a chart leak into deliberate practice.

The action half of the leak loop: a stateless quiz keyed to a player's leak
spot (scenario + position). It serves hands at that spot and grades the
player's fold/call/raise against the same solver charts the leak finder uses
(`preflop_reference`), so practice and diagnosis share one standard.

No game engine and no hand-state reconstruction — just sample spots, grade
answers. Depth/seats are fixed to a clean teaching baseline (100bb 6-max), the
canonical opening reference.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

from poker.hand_ranges import ALL_STARTING_HANDS
from poker.strategy.preflop_reference import reference_strategy

# Teaching baseline: standard deep-stack 6-max. The drill teaches the canonical
# opening/defending discipline, not a depth-specific adjustment.
DRILL_DEPTH_BB = 100
DRILL_PLAYERS = 6

# Verdict tiers by how often the chart takes the player's chosen action.
GOOD_MIN = 0.30   # a real part of the solver's strategy here
THIN_MIN = 0.10   # occasionally fine, but not the main line
# below THIN_MIN → 'leak' (the solver almost never does this)

_ACTIONS = ('fold', 'call', 'raise')


def _reference(scenario: str, position: str, hand: str) -> Optional[Dict[str, float]]:
    """Bucketed chart freqs for a drill spot (opener-agnostic, baseline depth)."""
    return reference_strategy(
        hand, position, scenario, None, DRILL_DEPTH_BB, DRILL_PLAYERS
    )


def sample_drill_spots(
    scenario: str, position: str, n: int = 10, *, rng: Optional[random.Random] = None
) -> List[dict]:
    """Sample up to `n` distinct gradeable hands for a (scenario, position) spot.

    Filters to hands the chart actually covers here, so every served spot can be
    graded. Order randomized so the drill varies run to run.
    """
    rng = rng or random.Random()
    gradeable = [h for h in ALL_STARTING_HANDS if _reference(scenario, position, h) is not None]
    rng.shuffle(gradeable)
    return [
        {
            'scenario': scenario,
            'position': position,
            'hand': hand,
            'depth_bb': DRILL_DEPTH_BB,
            'num_players': DRILL_PLAYERS,
        }
        for hand in gradeable[:n]
    ]


def grade_drill_answer(scenario: str, position: str, hand: str, action: str) -> Optional[dict]:
    """Grade a fold/call/raise against the chart. None if the spot isn't gradeable.

    Returns ``{verdict, action, your_freq, chart_freq, primary_action}`` where
    verdict is good / thin / leak by how often the chart takes `action` here.
    """
    action = (action or '').strip().lower()
    if action not in _ACTIONS:
        return None
    ref = _reference(scenario, position, hand)
    if ref is None:
        return None
    freq = ref[action]
    verdict = 'good' if freq >= GOOD_MIN else 'thin' if freq >= THIN_MIN else 'leak'
    primary = max(ref, key=ref.get)
    return {
        'verdict': verdict,
        'action': action,
        'your_freq': round(freq, 3),
        'chart_freq': {k: round(v, 3) for k, v in ref.items()},
        'primary_action': primary,
    }


def pick_drill_leak(leak_set: dict) -> Optional[dict]:
    """Choose which confirmed leak to drill from a get_owner_chart_leak_set().

    Prefers a specific-hand leak, else the strongest spot tendency. Returns
    ``{scenario, position, kind}`` or None when there's nothing confirmed.
    """
    by_hand = leak_set.get('by_hand') or {}
    by_spot = leak_set.get('by_spot') or {}
    for (scenario, position, _hand), info in by_hand.items():
        return {'scenario': scenario, 'position': position, 'kind': info['kind']}
    for (scenario, position), info in by_spot.items():
        return {'scenario': scenario, 'position': position, 'kind': info['kind']}
    return None
