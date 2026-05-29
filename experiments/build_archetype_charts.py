#!/usr/bin/env python3
"""Build width-tiered per-archetype preflop charts from the base 6-max chart.

The personality *distortion* layer (deviation_profiles.py) has a hard ceiling:
a bounded logit offset cannot OPEN a hand the base chart folds ~100% (no mass
to amplify; the per-action cap pins it near 0). That's why the loose archetypes
top out ~32% VPIP on the standard chart. The architecture fix (see
docs/plans/PERSONALITY_PRICING_AND_VARIETY.md) is a few width-tiered preflop
TABLES selected by archetype tier, with distortion layered on top for flavor.

This generator emits two new tiers derived from preflop_100bb_6max.json:

  * `_loose.json`   — wide RFI at EVERY position + wider vs_open/vs_3bet
    continues (more 3-bets AND flats). For LAG / Maniac (loose-aggressive);
    distortion adds the aggression flavor on top.
  * `_station.json` — moderate RFI but heavy FLAT-CALLING vs_open/vs_3bet
    (fold mass -> call, raises kept low). High VPIP, low PFR, high WtSD — a
    real calling station, which distortion alone cannot produce (looseness
    boosts call AND raise equally, and RFI is open-or-fold so looseness there
    only adds raises -> PFR rises with VPIP).

RFI is open-or-fold (no limp in the chart), so a position's open set fully
defines its RFI row: open hands -> {raise_2.5bb: 1.0}, else {fold: 1.0}.
vs_open / vs_3bet / vs_4bet rows are transformed in place (fold mass
redistributed) so the file shape matches the base exactly (all 169 hands per
node, all scenarios present).

Run inside the backend container:
    docker compose exec -T backend python -m experiments.build_archetype_charts
"""

import copy
import json
import os

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'poker',
    'strategy',
    'data',
)
_BASE = os.path.join(_DATA_DIR, 'preflop_100bb_6max.json')
_TIGHT = os.path.join(_DATA_DIR, 'preflop_100bb_6max_tight_rfi.json')

_PAIRS = ['AA', 'KK', 'QQ', 'JJ', 'TT', '99', '88', '77', '66', '55', '44', '33', '22']
_SUITED_A = ['AKs', 'AQs', 'AJs', 'ATs', 'A9s', 'A8s', 'A7s', 'A6s', 'A5s', 'A4s', 'A3s', 'A2s']
_OFFSUIT_A = ['AKo', 'AQo', 'AJo', 'ATo', 'A9o', 'A8o', 'A7o', 'A6o', 'A5o', 'A4o', 'A3o', 'A2o']
_SUITED_K = ['KQs', 'KJs', 'KTs', 'K9s', 'K8s', 'K7s', 'K6s', 'K5s', 'K4s', 'K3s', 'K2s']
_SUITED_Q = ['QJs', 'QTs', 'Q9s', 'Q8s', 'Q7s', 'Q6s', 'Q5s', 'Q4s', 'Q3s', 'Q2s']
_SUITED_J = ['JTs', 'J9s', 'J8s', 'J7s', 'J6s']
_SUITED_T = ['T9s', 'T8s', 'T7s', 'T6s']
_CONNECTORS = ['98s', '97s', '96s', '87s', '86s', '85s', '76s', '75s', '65s', '64s', '54s', '53s', '43s', '32s']

# ── Loose-aggressive RFI open sets (LAG / Maniac base width) ────────────────
# Wide at EVERY position; maniacs/LAGs do not respect position much. These are
# deliberately caricature-wide (the variety/skill-gradient end). Distortion
# layers aggression on top — it cannot widen RFI further (open-or-fold), so the
# table carries the VPIP envelope.

_LOOSE_UTG = set(  # ~30%
    _PAIRS + _SUITED_A
    + ['KQs', 'KJs', 'KTs', 'K9s', 'K8s', 'K7s']
    + ['QJs', 'QTs', 'Q9s', 'Q8s']
    + ['JTs', 'J9s', 'J8s']
    + ['T9s', 'T8s']
    + ['98s', '97s', '87s', '86s', '76s', '65s', '54s']
    + ['AKo', 'AQo', 'AJo', 'ATo', 'A9o']
    + ['KQo', 'KJo', 'KTo']
    + ['QJo', 'QTo']
    + ['JTo']
)
_LOOSE_HJ = _LOOSE_UTG | set(  # ~37%
    ['K6s', 'K5s', 'Q7s', 'Q6s', 'J7s', 'T7s', '96s', '75s', '64s', '53s', '43s']
    + ['A8o', 'A7o', 'A5o', 'K9o', 'Q9o', 'J9o', 'T9o', '98o']
)
_LOOSE_CO = _LOOSE_HJ | set(  # ~46%
    ['K4s', 'K3s', 'Q5s', 'Q4s', 'J6s', 'T6s', '85s', '32s']
    + ['A6o', 'A4o', 'A3o', 'A2o', 'K8o', 'K7o', 'Q8o', 'J8o', 'T8o', '87o', '76o']
)
_LOOSE_BTN = _LOOSE_CO | set(  # ~62%
    ['K2s', 'Q3s', 'Q2s', '95s', '74s', '63s', '52s', '42s']
    + ['K6o', 'K5o', 'K4o', 'Q7o', 'Q6o', 'J7o', 'T7o', '97o', '86o', '65o', '54o']
)
_LOOSE_SB = _LOOSE_CO | set(  # ~55% (no postflop position -> a touch tighter than BTN)
    ['K2s', 'Q3s', 'Q2s', '95s', '74s']
    + ['K6o', 'K5o', 'Q7o', 'J7o', 'T7o', '97o', '86o', '65o']
)

_LOOSE_RFI = {
    'UTG': _LOOSE_UTG,
    'HJ': _LOOSE_HJ,
    'CO': _LOOSE_CO,
    'BTN': _LOOSE_BTN,
    'SB': _LOOSE_SB,
}

# ── Loose-mid RFI (LAG) — between standard (~25% VPIP) and loose (~50%) ──────
# A textbook LAG sits ~26-34% VPIP / 20-28% PFR, between TAG and the maniac. The
# loose table overshoots (~54%), making LAG indistinguishable from Maniac, so
# LAG gets its own mid tier built by a position-shift of the loose sets (each
# seat opens the loose range of the seat one earlier = a notch tighter), with a
# trimmed UTG.
_LOOSE_MID_UTG = _LOOSE_UTG - {'A9o', 'JTo', 'QTo', '97s', '86s', 'T8s', 'J8s', 'J9s', 'Q8s'}
_LOOSE_MID = {
    'UTG': _LOOSE_MID_UTG,
    'HJ': _LOOSE_MID_UTG,
    'CO': _LOOSE_UTG,
    'BTN': _LOOSE_HJ,
    'SB': _LOOSE_UTG,
}

# Station RFI: keep it close to the *base* chart's opens (a station is not an
# aggressive opener — its volume comes from flatting, not raising), but make
# sure it at least opens the loose CO/BTN/SB the base already does. We reuse the
# base RFI rows verbatim (transform only the facing-action rows below).


def _combos(hand: str) -> int:
    if len(hand) == 2:
        return 6
    return 4 if hand[2] == 's' else 12


def _rfi_pct(pos_dict: dict) -> float:
    tot = opened = 0.0
    for hand, actions in pos_dict.items():
        c = _combos(hand)
        tot += c
        opened += c * sum(
            p for a, p in actions.items() if a.startswith('raise') or a in ('all_in', 'jam')
        )
    return 100.0 * opened / tot if tot else 0.0


def _vpip_pct(pos_dict: dict) -> float:
    """VPIP for a facing-action node: combo-weighted (call + raise + jam)."""
    tot = vpip = 0.0
    for hand, actions in pos_dict.items():
        c = _combos(hand)
        tot += c
        vpip += c * sum(
            p for a, p in actions.items()
            if a == 'call' or a.startswith('raise') or a in ('all_in', 'jam')
        )
    return 100.0 * vpip / tot if tot else 0.0


def _set_rfi(data: dict, rfi_map: dict):
    for pos, open_set in rfi_map.items():
        pos_dict = data['rfi'][pos]
        for hand in pos_dict:
            pos_dict[hand] = {'raise_2.5bb': 1.0} if hand in open_set else {'fold': 1.0}


def _loosen_facing(actions: dict, keep_fold: float) -> dict:
    """Loose-aggressive transform of a facing-action row: cut fold to
    `keep_fold` * original, split the freed mass between call and raise
    favouring raise (60/40). Hands the base pure-folds (fold==1.0) stay folded
    — we don't invent opens the chart never had (avoids opening 72o).
    """
    fold = actions.get('fold', 0.0)
    if fold >= 0.999:  # pure fold -> leave as is
        return dict(actions)
    new_fold = fold * keep_fold
    freed = fold - new_fold
    out = dict(actions)
    out['fold'] = new_fold
    # find a raise key present in the row (raise_3x / raise_2.2x / jam) else call-only
    raise_keys = [a for a in actions if a.startswith('raise') or a in ('all_in', 'jam')]
    if raise_keys:
        rk = raise_keys[0]
        out[rk] = out.get(rk, 0.0) + freed * 0.4
        out['call'] = out.get('call', 0.0) + freed * 0.6
    else:
        out['call'] = out.get('call', 0.0) + freed
    return out


def _station_facing(actions: dict, keep_fold: float) -> dict:
    """Calling-station transform: cut fold to `keep_fold` * original and put
    ~ALL freed mass into CALL (a hair into raise so it isn't perfectly face-up).
    Produces high VPIP via flats with low PFR. Pure-fold hands stay folded.
    """
    fold = actions.get('fold', 0.0)
    if fold >= 0.999:
        return dict(actions)
    new_fold = fold * keep_fold
    freed = fold - new_fold
    out = dict(actions)
    out['fold'] = new_fold
    raise_keys = [a for a in actions if a.startswith('raise') or a in ('all_in', 'jam')]
    if raise_keys:
        rk = raise_keys[0]
        out[rk] = out.get(rk, 0.0) + freed * 0.08
        out['call'] = out.get('call', 0.0) + freed * 0.92
    else:
        out['call'] = out.get('call', 0.0) + freed
    return out


def _transform_facing(data: dict, fn, keep_fold_by_scenario: dict):
    for scenario, keep_fold in keep_fold_by_scenario.items():
        for pos_dict in data[scenario].values():
            for hand in pos_dict:
                pos_dict[hand] = fn(pos_dict[hand], keep_fold)


def build_loose(base: dict) -> dict:
    data = copy.deepcopy(base)
    _set_rfi(data, _LOOSE_RFI)
    # widen continues: keep 45% of fold vs_open, 60% vs_3bet, 75% vs_4bet
    _transform_facing(data, _loosen_facing, {'vs_open': 0.45, 'vs_3bet': 0.60, 'vs_4bet': 0.75})
    return data


def build_loose_mid(base: dict) -> dict:
    """LAG tier — moderately wide RFI + moderately wide continues, landing
    between TAG (~25% VPIP) and Maniac (~50%)."""
    data = copy.deepcopy(base)
    _set_rfi(data, _LOOSE_MID)
    _transform_facing(data, _loosen_facing, {'vs_open': 0.72, 'vs_3bet': 0.80, 'vs_4bet': 0.86})
    return data


def build_station(base: dict) -> dict:
    data = copy.deepcopy(base)
    # RFI: a station is NOT a wild opener — it opens few hands (low PFR) and gets
    # its volume from flatting. Borrow the tight chart's RFI rows so PFR lands in
    # the textbook ~8-12% band; then flat very wide vs opens (fold->call).
    with open(_TIGHT) as f:
        tight = json.load(f)
    data['rfi'] = copy.deepcopy(tight['rfi'])
    _transform_facing(data, _station_facing, {'vs_open': 0.30, 'vs_3bet': 0.55, 'vs_4bet': 0.80})
    return data


def build_weak_station(base: dict) -> dict:
    """The weakest realistic fish (the $2-tier trickle): the station shape pushed
    to the believable floor — same tight-ish RFI (still not a wild opener) but
    flatting almost everything that has any non-fold mass vs a raise (keep only
    10% of the fold). Pure-fold trash (72o etc.) STAYS folded — the base chart's
    zeros + the math/defense floors are what keep even this a 'drunk tourist who
    can't fold,' not a call-anything bot. Pairs with the `weak_fish` deviation
    profile (sticky + over_bluff + can't-fold). See docs/plans/FISH_AS_CALLING_STATION.md.
    """
    data = copy.deepcopy(base)
    with open(_TIGHT) as f:
        tight = json.load(f)
    data['rfi'] = copy.deepcopy(tight['rfi'])
    _transform_facing(data, _station_facing, {'vs_open': 0.10, 'vs_3bet': 0.35, 'vs_4bet': 0.65})
    return data


def _write(data: dict, name: str):
    out = os.path.join(_DATA_DIR, name)
    with open(out, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"wrote {out}")
    for pos in ('UTG', 'HJ', 'CO', 'BTN', 'SB'):
        print(f"  rfi.{pos}: {_rfi_pct(data['rfi'][pos]):.1f}%")
    for sc in ('vs_open', 'vs_3bet'):
        pcts = [_vpip_pct(pd) for pd in data[sc].values()]
        print(f"  {sc} VPIP (avg over nodes): {sum(pcts)/len(pcts):.1f}%")


def main():
    with open(_BASE) as f:
        base = json.load(f)
    print("=== LOOSE (Maniac) ===")
    _write(build_loose(base), 'preflop_100bb_6max_loose.json')
    print("=== LOOSE-MID (LAG) ===")
    _write(build_loose_mid(base), 'preflop_100bb_6max_loose_mid.json')
    print("=== STATION (Calling Station) ===")
    _write(build_station(base), 'preflop_100bb_6max_station.json')
    print("=== WEAK STATION ($2 weak fish) ===")
    _write(build_weak_station(base), 'preflop_100bb_6max_weak_station.json')


if __name__ == '__main__':
    main()
