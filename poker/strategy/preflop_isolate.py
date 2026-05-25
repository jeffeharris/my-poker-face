"""Preflop ENTRY sharpening: shift OOP `vs_open` flat-calls to 3-bets.

docs/plans/STRUCTURAL_PASSIVITY_PLAN.md §9 established that the tiered bot's
postflop passivity is locked in upstream: it flat-calls `vs_open` into
multiway pots and so is almost never the lone aggressor with initiative
(83% of its "had-initiative" postflop spots are full-ring multiway). The
proven lever for this kind of problem was commit `8bb880df`
("tighten OOP vs_open flat-calls", +17 bb/100), which shifted 60% of the
OOP `vs_open` `call` mass to `fold`.

This module is the complementary move: shift OOP `vs_open` `call` mass to
`raise_3x` (3-bet to isolate into a heads-up pot with initiative) rather than
to `fold`. Isolating does double duty — it removes the marginal multiway spots
*and* manufactures the HU-with-initiative spots the multi-street barrel layer
(multistreet_context.py) needs to fire.

Scope mirrors `fold_more` exactly: OOP defenders only (`SB`, `HJ`, `CO`).
IP (`BTN`) and the closing `BB` keep their flat-calls (flatting in position /
at a price is correct). Applied in-memory to a loaded `StrategyTable` so an
A/B can compare control-entry vs isolate-entry without a second 16k-line JSON
(mirrors the multiway control-arm pattern). If it proves out, the same
transform can be baked into the JSON as a shipped chart edit.
"""

from typing import Dict, Iterable

from .strategy_profile import StrategyProfile
from .strategy_table import StrategyTable

# OOP defenders — same scope as the fold_more change (8bb880df).
ISOLATE_POSITIONS = ('SB', 'HJ', 'CO')

# 3-bet action label used by the vs_open chart rows.
RAISE_ACTION = 'raise_3x'


def transform_vs_open_to_isolate(
    preflop_data: Dict[str, StrategyProfile],
    *,
    shift_fraction: float = 0.7,
    positions: Iterable[str] = ISOLATE_POSITIONS,
    min_call: float = 0.10,
    raise_action: str = RAISE_ACTION,
) -> Dict[str, StrategyProfile]:
    """Return a new preflop-data dict with OOP `vs_open` call mass shifted to
    `raise_3x`.

    For each `vs_open|{pos}|{opener}|{hand}` node where `pos` is an OOP
    defender and the row flat-calls at least `min_call`, move
    `shift_fraction` of the `call` mass into `raise_3x` (3-bet to isolate);
    `fold` is left unchanged (the fold tightening from 8bb880df stays baked
    in). Rows below `min_call` (already near-pure fold) are untouched so we
    don't 3-bet the bottom of the range. Non-`vs_open` / IP / BB / RFI rows
    pass through unchanged.

    Mirrors 8bb880df's mechanic, redirecting to `raise_3x` instead of `fold`:
        new_call  = old_call * (1 - shift_fraction)
        new_raise = old_raise + old_call * shift_fraction
    """
    positions = set(positions)
    out: Dict[str, StrategyProfile] = {}
    for key, profile in preflop_data.items():
        parts = key.split('|')  # scenario|position|opener|hand
        if (
            len(parts) == 4
            and parts[0] == 'vs_open'
            and parts[1] in positions
        ):
            probs = dict(profile.action_probabilities)
            call = probs.get('call', 0.0)
            if call >= min_call and raise_action in probs:
                moved = call * shift_fraction
                probs['call'] = call - moved
                probs[raise_action] = probs.get(raise_action, 0.0) + moved
                out[key] = StrategyProfile(action_probabilities=probs)
                continue
        out[key] = profile
    return out


def build_isolation_table(table: StrategyTable, **kwargs) -> StrategyTable:
    """Return a new StrategyTable with the isolate transform applied to its
    preflop data; postflop data is shared unchanged. Non-destructive — the
    input table is untouched (so an A/B can hold both arms).
    """
    new_preflop = transform_vs_open_to_isolate(table._preflop, **kwargs)
    return StrategyTable(new_preflop, table._postflop)
