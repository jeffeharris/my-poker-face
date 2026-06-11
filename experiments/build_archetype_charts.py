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
_CONNECTORS = [
    '98s',
    '97s',
    '96s',
    '87s',
    '86s',
    '85s',
    '76s',
    '75s',
    '65s',
    '64s',
    '54s',
    '53s',
    '43s',
    '32s',
]

# ── Loose-aggressive RFI open sets (LAG / Maniac base width) ────────────────
# Wide at EVERY position; maniacs/LAGs do not respect position much. These are
# deliberately caricature-wide (the variety/skill-gradient end). Distortion
# layers aggression on top — it cannot widen RFI further (open-or-fold), so the
# table carries the VPIP envelope.

_LOOSE_UTG = set(  # ~30%
    _PAIRS
    + _SUITED_A
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
    ['K2s', 'Q3s', 'Q2s', '95s', '74s'] + ['K6o', 'K5o', 'Q7o', 'J7o', 'T7o', '97o', '86o', '65o']
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
            p
            for a, p in actions.items()
            if a == 'call' or a.startswith('raise') or a in ('all_in', 'jam')
        )
    return 100.0 * vpip / tot if tot else 0.0


def _set_rfi(data: dict, rfi_map: dict):
    for pos, open_set in rfi_map.items():
        pos_dict = data['rfi'][pos]
        for hand in pos_dict:
            pos_dict[hand] = {'raise_2.5bb': 1.0} if hand in open_set else {'fold': 1.0}


def _loosen_facing(actions: dict, keep_fold: float, raise_share: float = 0.4) -> dict:
    """Loose-aggressive transform of a facing-action row: cut fold to
    `keep_fold` * original, split the freed mass between call and raise.
    `raise_share` is the fraction of freed mass that becomes RE-RAISE (3-bet /
    4-bet); the rest becomes flat-call. Default 0.4 (the original 60/40 call/raise
    split). LOWERING raise_share cuts re-raise frequency while keeping the SAME
    continue rate (VPIP) — it just flats more — which is the clean knob for taming
    a tier's 3-bet without narrowing its range. Hands the base pure-folds
    (fold==1.0) stay folded (we don't invent opens the chart never had).
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
        out[rk] = out.get(rk, 0.0) + freed * raise_share
        out['call'] = out.get('call', 0.0) + freed * (1.0 - raise_share)
    else:
        out['call'] = out.get('call', 0.0) + freed
    return out


# ── Loose-tier 3-bet promotion ───────────────────────────────────────────────
# The base vs_open is deliberately CONCENTRATED (raise mass only on real value +
# suited bluffs) so it doesn't pollute the vs_3bet villain model — but that means
# _loosen_facing has no raise key to amplify on the wide flat range, so LAG/maniac
# can only flat those hands, never 3-bet them. The looseness lives HERE: for a
# loose tier, promote a curated pool of FLATTED hands into 3-bets. Trash is
# excluded and (pure-fold in the base) hard-masked regardless.
_PROMOTE_3BET_OFFSUIT = {'AKo', 'AQo', 'AJo', 'KQo', 'KJo', 'QJo'}


def _is_promotable_3bet(hand: str) -> bool:
    """Suited, a pair, or a playable offsuit broadway — never offsuit trash."""
    return len(hand) == 2 or hand[2] == 's' or hand in _PROMOTE_3BET_OFFSUIT


def _promote_3bet(row: dict, frac: float, hand: str) -> dict:
    """Move `frac` of a flatted hand's CALL mass into raise_3x (a 3-bet). Only for
    curated hands the base FLATS (call present, no raise key); skips pure-fold
    (masked) and hands that already 3-bet. VPIP (continue rate) is unchanged."""
    if frac <= 0 or not _is_promotable_3bet(hand):
        return row
    if row.get('fold', 0.0) >= 0.999 or row.get('raise_3x', 0.0) > 0.0:
        return row
    call = row.get('call', 0.0)
    if call <= 0:
        return row
    promoted = call * frac
    out = dict(row)
    out['raise_3x'] = promoted
    out['call'] = call - promoted
    return out


# ── Station / fish call mask ──────────────────────────────────────────────────
# The base now pure-folds MORE hands (concentrated), and _station_facing can't
# touch pure-fold rows — so a station/fish gets masked into TAG-like tightness.
# A station's identity is a wide CALL range, so it INVENTS calls on a curated pool
# regardless of the base (calls, never raises — distinctness comes from flatting).
_RANKS = 'AKQJT98765432'


def _is_station_callable(hand: str) -> bool:
    """A station/fish flats a wide range: any suited, any pair, and offsuit
    aces/kings/broadways/connectors. Excludes only the worst offsuit junk."""
    if len(hand) == 2 or hand[2] == 's':
        return True
    hi, lo = _RANKS.index(hand[0]), _RANKS.index(hand[1])  # 0=A … 12=2
    return hi <= 1 or (hi <= 4 and lo <= 5) or abs(hi - lo) <= 2  # Ax/Kx, broadway, connector


def _invent_call(row: dict, frac: float, hand: str) -> dict:
    """Ensure a curated station/fish hand calls at least `frac`, OVERRIDING the
    pure-fold mask (the wide call range is the station's identity, independent of
    the concentrated base). Never overrides the hand's own aggression."""
    if frac <= 0 or not _is_station_callable(hand):
        return row
    if any(row.get(k, 0.0) > 0 for k in ('raise_3x', 'raise_2.2x', 'jam')):
        return row
    if row.get('call', 0.0) >= frac:
        return row
    return {'call': frac, 'fold': round(1.0 - frac, 4)}


def _station_facing(actions: dict, keep_fold: float, damp_raise: float = 0.0) -> dict:
    """Calling-station transform: cut fold to `keep_fold` * original and put
    ~ALL freed mass into CALL (a hair into raise so it isn't perfectly face-up).
    Produces high VPIP via flats with low PFR. Pure-fold hands stay folded.

    `damp_raise` (in [0, 1]) additionally routes that fraction of the row's
    EXISTING re-raise mass into call. The base chart 3-bets its premiums (AA/KK
    raise ~85% facing an open); a calling station TRAPS those — it flats, it does
    not 3-bet. Without this the freed-fold redistribution alone left the station
    inheriting the base's ~18% facing-open 3-bet (realized ~13%, band 1–5). At
    0.85 the station 3-bets premiums ~13% of the time it holds them — a stray,
    readable spike, not a reg's polarized 3-bet game. vs_open/vs_3bet only; RFI
    (open-or-fold) is untouched so PFR is unchanged.
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
        existing_raise = out.get(rk, 0.0)
        moved = existing_raise * damp_raise
        out[rk] = existing_raise - moved + freed * 0.08
        out['call'] = out.get('call', 0.0) + freed * 0.92 + moved
    else:
        out['call'] = out.get('call', 0.0) + freed
    return out


def _transform_facing(
    data: dict,
    fn,
    keep_fold_by_scenario: dict,
    extra_by_scenario: "dict | None" = None,
    promote_3bet_by_scenario: "dict | None" = None,
    invent_call_by_scenario: "dict | None" = None,
):
    """Apply `fn(actions, keep_fold, **extra)` to every row of each scenario.
    `extra_by_scenario` (optional) supplies per-scenario kwargs — e.g. a lower
    `raise_share` at vs_open/vs_3bet to cut 3-bet/4-bet frequency without
    changing the continue (VPIP) rate. `promote_3bet_by_scenario` (optional, loose
    tiers): per-scenario fraction of flatted-hand call mass promoted into 3-bets
    (_promote_3bet). `invent_call_by_scenario` (optional, station/fish + loose
    fold-to-3bet): per-scenario min call frequency invented on the curated pool,
    overriding the pure-fold mask (_invent_call)."""
    extra_by_scenario = extra_by_scenario or {}
    promote_3bet_by_scenario = promote_3bet_by_scenario or {}
    invent_call_by_scenario = invent_call_by_scenario or {}
    for scenario, keep_fold in keep_fold_by_scenario.items():
        extra = extra_by_scenario.get(scenario, {})
        promote = promote_3bet_by_scenario.get(scenario, 0.0)
        invent = invent_call_by_scenario.get(scenario, 0.0)
        for pos_dict in data[scenario].values():
            for hand in pos_dict:
                row = fn(pos_dict[hand], keep_fold, **extra)
                if promote:
                    row = _promote_3bet(row, promote, hand)
                if invent:
                    row = _invent_call(row, invent, hand)
                pos_dict[hand] = row


def build_loose(base: dict) -> dict:
    data = copy.deepcopy(base)
    _set_rfi(data, _LOOSE_RFI)
    # widen continues: keep 45% of fold vs_open, 60% vs_3bet, 75% vs_4bet.
    # vs_3bet raise_share is bumped to 0.70 so the maniac amplifies the base's
    # (suited-only) bluff-4-bet mass into its wide polarized 4-bet — the
    # believable maniac (no offsuit trash 4-bets; see build_vs3bet_defense.py).
    _transform_facing(
        data,
        _loosen_facing,
        {'vs_open': 0.30, 'vs_3bet': 0.60, 'vs_4bet': 0.75},
        extra_by_scenario={'vs_3bet': {'raise_share': 0.70}},
        # Maniac 3-bets a wide curated pool the concentrated base only flats.
        promote_3bet_by_scenario={'vs_open': 1.0},
        # Maniac continues wide vs a 3-bet (call-heavy + the amplified 4-bet) so it
        # doesn't over-fold — invent calls on the curated pool past the mask.
        invent_call_by_scenario={'vs_3bet': 0.45},
    )
    return data


def build_loose_mid(base: dict) -> dict:
    """LAG tier — moderately wide RFI + moderately wide continues, landing
    between TAG (~25% VPIP) and Maniac (~50%).

    LAG's identity is "plays wide", not "re-raises everything": the default 0.4
    raise-share pushed realized facing-open 3-bet to ~32% (target 16–26). We keep
    the SAME continue rate (keep_fold unchanged → VPIP unchanged) but route more
    of the freed mass to FLAT-CALL via a lower raise_share at the re-raise nodes,
    so LAG flats wide instead of 3-betting wide. See ARCHETYPE_SHAPING_FINDINGS.md.
    """
    data = copy.deepcopy(base)
    _set_rfi(data, _LOOSE_MID)
    _transform_facing(
        data,
        _loosen_facing,
        {'vs_open': 0.72, 'vs_3bet': 0.80, 'vs_4bet': 0.86},
        extra_by_scenario={
            'vs_open': {'raise_share': 0.18},
            'vs_3bet': {'raise_share': 0.25},
            'vs_4bet': {'raise_share': 0.30},
        },
        # LAG 3-bets a moderate curated pool the base flats — less than the maniac.
        promote_3bet_by_scenario={'vs_open': 0.45},
        # LAG continues wider vs a 3-bet (call-heavy) — invent calls past the mask.
        invent_call_by_scenario={'vs_3bet': 0.35},
    )
    return data


def build_station(base: dict) -> dict:
    data = copy.deepcopy(base)
    # RFI: a station is NOT a wild opener — it opens few hands (low PFR) and gets
    # its volume from flatting. Borrow the tight chart's RFI rows so PFR lands in
    # the textbook ~8-12% band; then flat very wide vs opens (fold->call).
    with open(_TIGHT) as f:
        tight = json.load(f)
    data['rfi'] = copy.deepcopy(tight['rfi'])
    _transform_facing(
        data,
        _station_facing,
        # vs_3bet keep_fold 0.55→0.63: with the equity-graded vs_3bet (which
        # flat-calls a real medium range rather than the stub's uniform blob),
        # 0.55 widened the station's 3-bet defense past its fold-to-3bet floor.
        {'vs_open': 0.30, 'vs_3bet': 0.63, 'vs_4bet': 0.80},
        # Trap the premiums the base chart 3-bets: a station does not re-raise.
        # Damps facing-open 3-bet ~18%→~3% and facing-3bet 4-bet ~14%→~3%.
        extra_by_scenario={
            'vs_open': {'damp_raise': 0.85},
            'vs_3bet': {'damp_raise': 0.85},
        },
        # A station's identity is a WIDE call range; invent it past the pure-fold
        # mask (the concentrated base would otherwise collapse it to a passive TAG).
        invent_call_by_scenario={'vs_open': 0.90, 'vs_3bet': 0.42},
    )
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
    _transform_facing(
        data,
        _station_facing,
        # vs_3bet keep_fold 0.35→0.45 (same reason as build_station): the graded
        # vs_3bet needs a touch more fold kept to stay above the fold-to-3bet floor.
        {'vs_open': 0.10, 'vs_3bet': 0.62, 'vs_4bet': 0.65},
        # Same trap-the-premiums damp as the station, a touch softer (the $2 fish
        # is a hair more spew-prone). Damps facing-open 3-bet ~19%→~4%.
        extra_by_scenario={
            'vs_open': {'damp_raise': 0.80},
            'vs_3bet': {'damp_raise': 0.80},
        },
        # The splashiest fish — an even wider invented call range than the station.
        invent_call_by_scenario={'vs_open': 0.78},
    )
    return data


def build_tight(base: dict) -> dict:
    """nit / rock tier — the tight RFI (kept verbatim) over the base facing rows
    with their premium re-raise mass TRIMMED into flat-calls.

    nit and rock both read ``preflop_100bb_6max_tight_rfi.json``, whose facing
    rows were a verbatim copy of the standard base (~14.5% facing-open 3-bet,
    premiums raising ~85%). The per-action distortion cap (0.30) cannot pull an
    0.85 raise down to a nit's ~2-7% 3-bet — so realized 3-bet stuck high (nit
    ~9%, rock ~12%, both over band). The chart is the lever: damp the existing
    re-raise mass into CALL (a nit/rock TRAPS a premium facing an open — it does
    not 3-bet it, and it certainly does not fold it), keeping the fold mass
    untouched (``keep_fold=1.0``) so the tight range is preserved. vs_open only is
    damped hard; vs_3bet lighter so the 4-bet stays in band. RFI is untouched, so
    VPIP/PFR move only by the small premium-flat shift. See
    docs/technical/ARCHETYPE_SHAPING_FINDINGS.md (Knob 2, tight tier).
    """
    with open(_TIGHT) as f:
        tight = json.load(f)
    data = copy.deepcopy(base)
    data['rfi'] = copy.deepcopy(tight['rfi'])
    _transform_facing(
        data,
        _station_facing,
        # keep_fold=1.0: do NOT widen the tight range; only re-route raise->call.
        {'vs_open': 1.0, 'vs_3bet': 1.0, 'vs_4bet': 1.0},
        extra_by_scenario={
            'vs_open': {'damp_raise': 0.55},
            'vs_3bet': {'damp_raise': 0.45},
        },
    )
    return data


def _write(data: dict, name: str):
    # vs_squeeze (cold-caller-faces-a-squeeze) lives in the BASE chart only. The
    # archetype transforms don't reshape it, so a deep-copied base copy would carry
    # the untransformed base squeeze into every variant. Drop it instead: each
    # archetype's vs_squeeze lookup then falls back (StrategyTable.lookup_with_fallback)
    # to that archetype's OWN transformed vs_3bet — e.g. a station defends a squeeze
    # with its widened vs_3bet range, which is more on-archetype than a stale base copy.
    data.pop('vs_squeeze', None)
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
    # Tight tier first: it preserves the existing tight RFI and only trims the
    # facing-row re-raise mass (idempotent — facing rows derive from `base`, not
    # from the prior output). station/weak_station read this file's RFI, which is
    # unchanged, so order is safe either way.
    print("=== TIGHT (nit / rock) ===")
    _write(build_tight(base), 'preflop_100bb_6max_tight_rfi.json')
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
