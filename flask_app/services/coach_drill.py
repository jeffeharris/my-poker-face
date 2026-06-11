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

# Seats each scenario can be drilled from (BB never opens; UTG never faces an
# open). A ``position=mix`` request fans out over the whole set server-side so
# the client makes one call instead of one-per-seat (which bursts the limiter).
RFI_POSITIONS = ('UTG', 'HJ', 'CO', 'BTN', 'SB')
VS_OPEN_POSITIONS = ('HJ', 'CO', 'BTN', 'SB', 'BB')
# Which seats a `position=mix` request fans out over, per scenario. Explicit so a
# new scenario (e.g. vs_3bet / vs_4bet) can't silently inherit the RFI seat set —
# that would omit BB and include UTG, returning a malformed pool. Add a scenario
# here deliberately when its mixed drill ships.
_MIX_POSITIONS = {
    'rfi': RFI_POSITIONS,
    'vs_open': VS_OPEN_POSITIONS,
}
_MIX_TOKENS = ('mix', 'all', '')

# Verdict tiers by how often the chart takes the player's chosen action.
GOOD_MIN = 0.30  # a real part of the solver's strategy here
THIN_MIN = 0.10  # occasionally fine, but not the main line
# below THIN_MIN → 'leak' (the solver almost never does this)

_ACTIONS = ('fold', 'call', 'raise')


def _reference(
    scenario: str, position: str, hand: str, archetype: Optional[str] = None
) -> Optional[Dict[str, float]]:
    """Bucketed chart freqs for a drill spot (opener-agnostic, baseline depth).

    ``archetype`` grades against an opponent's width-tier chart ("what would a
    <archetype> do") instead of the baseline standard.
    """
    return reference_strategy(
        hand, position, scenario, None, DRILL_DEPTH_BB, DRILL_PLAYERS, archetype
    )


def sample_drill_spots(
    scenario: str,
    position: str,
    n: int = 10,
    *,
    rng: Optional[random.Random] = None,
    archetype: Optional[str] = None,
) -> List[dict]:
    """Sample up to `n` distinct gradeable hands for a (scenario, position) spot.

    Filters to hands the chart actually covers here, so every served spot can be
    graded. Order randomized so the drill varies run to run. ``archetype`` tags
    the spots and grades against that opponent's chart.
    """
    rng = rng or random.Random()
    gradeable = [
        h for h in ALL_STARTING_HANDS if _reference(scenario, position, h, archetype) is not None
    ]
    rng.shuffle(gradeable)
    return [
        {
            'scenario': scenario,
            'position': position,
            'hand': hand,
            'depth_bb': DRILL_DEPTH_BB,
            'num_players': DRILL_PLAYERS,
            'archetype': archetype,
        }
        for hand in gradeable[:n]
    ]


def sample_drill(
    scenario: str,
    position: str,
    n: int = 10,
    *,
    rng: Optional[random.Random] = None,
    archetype: Optional[str] = None,
) -> List[dict]:
    """Sample drill spots for a spot, expanding ``position='mix'`` server-side.

    A single seat samples just that seat (the leak-nudge path). ``mix``/``all``
    fans out over the scenario's full seat set and returns the combined,
    shuffled pool — one HTTP call instead of one-per-seat, which keeps a
    "mixed" drill from bursting the rate limiter.
    """
    rng = rng or random.Random()
    if position.strip().lower() in _MIX_TOKENS:
        seats = _MIX_POSITIONS.get(scenario)
        if seats is None:
            raise ValueError(f"position=mix is not supported for scenario {scenario!r}")
        spots: List[dict] = []
        for seat in seats:
            spots.extend(sample_drill_spots(scenario, seat, n=n, rng=rng, archetype=archetype))
        rng.shuffle(spots)
        return spots
    return sample_drill_spots(scenario, position, n=n, rng=rng, archetype=archetype)


def grade_drill_answer(
    scenario: str, position: str, hand: str, action: str, archetype: Optional[str] = None
) -> Optional[dict]:
    """Grade a fold/call/raise against the chart. None if the spot isn't gradeable.

    Returns ``{verdict, action, your_freq, chart_freq, primary_action}`` where
    verdict is good / thin / leak by how often the chart takes `action` here.
    ``archetype`` grades against that opponent's width-tier chart.
    """
    action = (action or '').strip().lower()
    if action not in _ACTIONS:
        return None
    ref = _reference(scenario, position, hand, archetype)
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
