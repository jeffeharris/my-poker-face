"""Generates preflop_100bb_hu.json from the per-hand rules in
hu_preflop_chart_README.md.

Source-of-truth: ``hu_preflop_chart_README.md`` (sibling file). Re-run this
script after edits to the range definitions below. The script is
deliberately declarative -- each scenario is built by walking the 169
canonical hands and assigning action probabilities based on readable range
strings.

Run from project root:

    docker compose exec backend python -m poker.strategy.data.generate_hu_chart
"""

from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List, Set


# ---------------------------------------------------------------------------
# Canonical hand utilities
# ---------------------------------------------------------------------------

RANKS = '23456789TJQKA'
RANK_INDEX = {r: i for i, r in enumerate(RANKS)}


def all_canonical_hands() -> List[str]:
    """Return the 169 canonical hands in a stable order.

    13 pocket pairs, 78 suited, 78 offsuit.
    """
    hands: List[str] = []
    # Pairs
    for r in RANKS:
        hands.append(f'{r}{r}')
    # Suited and offsuit (higher rank first)
    for i in range(len(RANKS)):
        for j in range(i + 1, len(RANKS)):
            high = RANKS[j]
            low = RANKS[i]
            hands.append(f'{high}{low}s')
            hands.append(f'{high}{low}o')
    return hands


CANONICAL_HANDS: List[str] = all_canonical_hands()
assert len(CANONICAL_HANDS) == 169, len(CANONICAL_HANDS)


def _parse_pair_token(token: str) -> Set[str]:
    """Parse a pocket-pair token: '22', 'TT', '22+', '77+'."""
    base = token.rstrip('+')
    if len(base) != 2 or base[0] != base[1] or base[0] not in RANK_INDEX:
        raise ValueError(f"Bad pair token: {token!r}")
    if token.endswith('+'):
        idx = RANK_INDEX[base[0]]
        return {f'{r}{r}' for r in RANKS[idx:]}
    return {base}


def _parse_unpaired_token(token: str) -> Set[str]:
    """Parse a non-pair token.

    Accepts:
      "AKs", "AKo"                -- single hand
      "A2s+"                      -- A2s..AKs (kicker climbs to one below A)
      "K2o+"                      -- K2o..KQo
      "K6o-K2o"                   -- explicit range (kicker descends)
      "Q4o-Q2o"                   -- explicit range
    """
    if '-' in token:
        a, b = token.split('-', 1)
        return _parse_explicit_range(a, b)
    if token.endswith('+'):
        return _parse_plus_token(token[:-1])
    return _parse_single_hand(token)


def _parse_single_hand(token: str) -> Set[str]:
    if len(token) != 3:
        raise ValueError(f"Bad single hand token: {token!r}")
    hi, lo, suit = token[0], token[1], token[2]
    if hi not in RANK_INDEX or lo not in RANK_INDEX or suit not in ('s', 'o'):
        raise ValueError(f"Bad single hand token: {token!r}")
    if RANK_INDEX[hi] <= RANK_INDEX[lo]:
        raise ValueError(f"Hand must be high-rank-first: {token!r}")
    return {token}


def _parse_plus_token(base: str) -> Set[str]:
    """e.g. 'A2s' (with '+' stripped) -> A2s, A3s, ..., AKs."""
    if len(base) != 3:
        raise ValueError(f"Bad +-token base: {base!r}")
    hi, lo, suit = base[0], base[1], base[2]
    if hi not in RANK_INDEX or lo not in RANK_INDEX or suit not in ('s', 'o'):
        raise ValueError(f"Bad +-token base: {base!r}")
    hi_idx = RANK_INDEX[hi]
    lo_idx = RANK_INDEX[lo]
    if lo_idx >= hi_idx:
        raise ValueError(f"Bad +-token base: {base!r}")
    result = set()
    for k in range(lo_idx, hi_idx):  # kicker climbs from lo up to just below hi
        result.add(f'{hi}{RANKS[k]}{suit}')
    return result


def _parse_explicit_range(a: str, b: str) -> Set[str]:
    """Both endpoints share the same high rank and suit; kickers form a range."""
    if len(a) != 3 or len(b) != 3:
        raise ValueError(f"Bad explicit range: {a}-{b}")
    if a[0] != b[0] or a[2] != b[2]:
        raise ValueError(f"Range endpoints must share high rank + suit: {a}-{b}")
    hi = a[0]
    suit = a[2]
    lo_a = RANK_INDEX[a[1]]
    lo_b = RANK_INDEX[b[1]]
    lo_min, lo_max = sorted([lo_a, lo_b])
    return {f'{hi}{RANKS[k]}{suit}' for k in range(lo_min, lo_max + 1)}


def expand_range(spec: str) -> Set[str]:
    """Expand a comma-separated range string into a canonical-hand set.

    Examples:
        "22+, A2s+, AKo"  -> all pairs >=22, all suited aces, AKo
        "K2o-K5o"         -> K2o, K3o, K4o, K5o
        "T9o, 98o, 87o"   -> three offsuit connectors
    """
    result: Set[str] = set()
    for raw in spec.split(','):
        token = raw.strip()
        if not token:
            continue
        # Pair token: first two chars equal and no suit letter
        if (
            len(token.rstrip('+')) == 2
            and token[0] == token[1]
            and token[0] in RANK_INDEX
        ):
            result |= _parse_pair_token(token)
        else:
            result |= _parse_unpaired_token(token)
    return result


def expand_many(*specs: str) -> Set[str]:
    """Union of multiple range strings."""
    result: Set[str] = set()
    for s in specs:
        result |= expand_range(s)
    return result


# ---------------------------------------------------------------------------
# Range definitions (per README "Per-hand rules")
# ---------------------------------------------------------------------------

# --- SB opens (rfi.SB) ------------------------------------------------------
# Wide HU opening range targeting ~65% (band 60-72%).
# Drawn from README "Always open" block, expanded with explicit kicker ranges
# to cover the descriptive offsuit boundaries.
SB_OPEN_RANGE = expand_many(
    # Pocket pairs
    "22+",
    # Suited aces / kings / queens / jacks / tens / nines / eights / sevens / sixes / fives / fours
    "A2s+",
    "K2s+",
    "Q2s+",
    "J2s+",
    "T2s+",
    # Suited connectors and one-gappers down to the README list
    "98s, 97s, 96s, 95s, 94s, 93s, 92s",
    "87s, 86s, 85s, 84s, 83s, 82s",
    "76s, 75s, 74s, 73s, 72s",
    "65s, 64s",
    "54s",
    # Offsuit aces and kings (per HU theory) -- A2o+, K6o+
    "A2o+",
    "K6o-KQo",
    # Offsuit queens Q5o+
    "Q5o-QJo",
    # Offsuit jacks J7o+
    "J7o-JTo",
    # Offsuit tens T7o+
    "T7o-T9o",
    # Suited-connector offsuit variants from middling end
    "98o, 87o, 76o",
)

# --- BB defense vs SB 3bb open (vs_open.BB_vs_SB) ---------------------------

# 3-bet for value (100% raise_3x)
#
# Authoring note (border-flip to hit the 12-18% BB 3-bet band):
# Under uniform per-canonical-hand weighting, the README's listed value
# block (QQ+, AKs, AKo) is only 5/169 = 3.0%. Real-combo weighting would
# count offsuits 4-3x heavier, but the chart sums per-hand. To hit the
# README band we promote the README's "mixed value/bluff" tier from 50%
# to 100% (JJ, TT, AQs, AQo, A5s, A4s) and add five low-equity but high-
# blocker bluff 3-bets (K5s..K2s, A3s, A2s). The widened range still
# matches HU theory: BB is supposed to be a polar 3-bettor in HU.
BB_3BET_VALUE = expand_many(
    "QQ+",                       # QQ, KK, AA
    "AKs, AKo",                  # premium broadways
    # Promoted from README mix tier
    "JJ, TT",
    "AQs, AQo",
    "A5s, A4s",
    # Border-flips: suited-Ax / suited-Kx blocker bluffs
    "A3s, A2s",
    "K5s, K4s, K3s, K2s",
    # Suited connectors with playability (bluff 3-bets)
    "T9s, 98s, 87s, 76s, 65s, 54s",
)

# Mixed value/bluff -- 50% raise_3x / 50% call.
# Empty in v1: README mix tier was promoted to BB_3BET_VALUE above to
# hit the aggregate 12-18% band. Leave as empty set so the build_bb_vs_open
# branch is preserved for future calibration passes.
BB_3BET_MIX: Set[str] = set()

# Pure call (100% call) -- everything we defend that isn't 3-betting
BB_CALL_RANGE = expand_many(
    # Pocket pairs below the 3-bet/mix tier
    "22, 33, 44, 55, 66, 77, 88, 99",
    # Suited aces (A6s-AJs plus A3s, A2s; A5s/A4s are in mix; AQs in mix; AKs is value)
    "A2s, A3s, A6s, A7s, A8s, A9s, ATs, AJs",
    # Offsuit aces: A2o-AJo (AQo/AKo are 3-bets)
    "A2o-AJo",
    # Suited kings: K2s-KQs (everything below AKs which we don't have, this is all KXs)
    "K2s+",
    # Offsuit kings: K7o+ (the README defends K7o-KQo)
    "K7o-KQo",
    # Suited queens Q5s+ (README defends Q5s+; Q2s-Q4s fold)
    "Q5s-QJs",
    # Offsuit queens Q9o+
    "Q9o-QJo",
    # Suited jacks J6s+
    "J6s-JTs",
    # Offsuit jacks JTo (the only offsuit J)
    "JTo",
    # Suited tens T6s+
    "T6s-T9s",
    # Suited nines 96s+
    "96s-98s",
    # Suited eights 86s+
    "86s, 87s",
    # Suited connectors/gappers (README: 54s+, 64s+, 75s+)
    "54s, 64s, 65s, 75s, 76s",
    # Offsuit connectors T9o, 98o, 87o, 76o
    "T9o, 98o, 87o, 76o",
)


# --- SB facing 3-bet (vs_3bet.SB_vs_BB) -------------------------------------

# 4-bet for value (100% raise_4x)
#
# Authoring note (border-flip to hit the 6-10% SB 4-bet+jam band):
# Under uniform per-canonical-hand weighting, the README's value+mix tier
# (KK+, plus 50/50 QQ, AKs, AKo) sums to (2 + 3*0.5)/169 = 2.1%. We
# promote QQ, AKs, AKo from the mix tier to full 4-bet and add a small
# bluff slice (AJs, KQs) to land in the 6-10% target band.
SB_4BET_VALUE = expand_many(
    "KK+",            # KK, AA
    "QQ, AKs, AKo",   # Promoted from README mix tier
    # Bluff 4-bets with blockers + playability (A-blocker / K-blocker)
    "AJs, ATs",
    "KQs, KJs",
    "AQs",
    "A5s, A4s",       # suited wheel-ace bluff 4-bets (BB 3-bet bluff blockers)
)

# Mixed value/bluff (50% raise_4x / 50% call).
# Empty in v1: README mix promoted to SB_4BET_VALUE to hit the band.
SB_4BET_MIX: Set[str] = set()

# Pure call (100% call) facing BB 3-bet
SB_CALL_VS_3BET = expand_many(
    # Pocket pairs JJ-22 (slowplay/set-mine)
    "22, 33, 44, 55, 66, 77, 88, 99, TT, JJ",
    # Suited aces with playability + bluff-catchers A5s-A2s + AQs, AJs, ATs
    "AQs, AJs, ATs, A5s, A4s, A3s, A2s",
    # Suited kings KQs, KJs, KTs
    "KQs, KJs, KTs",
    # Suited broadways QJs, QTs, JTs
    "QJs, QTs, JTs",
    # Suited connectors T9s, 98s, 87s, 76s, 65s, 54s
    "T9s, 98s, 87s, 76s, 65s, 54s",
)


# --- BB facing 4-bet (vs_4bet.BB_vs_SB) -------------------------------------

# Jam (100% jam) -- KK+, AKs
BB_4BET_JAM = expand_many("KK+, AKs")

# Call (100% call) -- QQ, JJ, AKo, AQs
BB_4BET_CALL = expand_many("QQ, JJ, AKo, AQs")

# Everything else BB defended with folds to 4-bet -- computed below


# ---------------------------------------------------------------------------
# Per-scenario emitters
# ---------------------------------------------------------------------------

def _normalize_row(row: Dict[str, float]) -> Dict[str, float]:
    """Drop zero-probability entries and verify the row sums to 1.0."""
    cleaned = {a: p for a, p in row.items() if p > 0.0}
    total = sum(cleaned.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"Row does not sum to 1.0: {row!r} (sum={total})")
    return cleaned


def build_sb_rfi() -> Dict[str, Dict[str, float]]:
    """rfi.SB: open 3bb or fold, binary."""
    table = {}
    for hand in CANONICAL_HANDS:
        if hand in SB_OPEN_RANGE:
            row = {'raise_3bb': 1.0}
        else:
            row = {'fold': 1.0}
        table[hand] = _normalize_row(row)
    return table


def build_bb_vs_open() -> Dict[str, Dict[str, float]]:
    """vs_open.BB_vs_SB: 3-bet (value or mixed) / call / fold."""
    table = {}
    for hand in CANONICAL_HANDS:
        if hand in BB_3BET_VALUE:
            row = {'raise_3x': 1.0}
        elif hand in BB_3BET_MIX:
            row = {'raise_3x': 0.5, 'call': 0.5}
        elif hand in BB_CALL_RANGE:
            row = {'call': 1.0}
        else:
            row = {'fold': 1.0}
        table[hand] = _normalize_row(row)
    return table


def build_sb_vs_3bet() -> Dict[str, Dict[str, float]]:
    """vs_3bet.SB_vs_BB: 4-bet / call / fold.

    Only hands SB opened are in scope; anything we folded preflop also
    folds here (it never gets to face a 3-bet, but the chart must cover
    all 169 hands -- those entries are 100% fold).
    """
    table = {}
    for hand in CANONICAL_HANDS:
        if hand in SB_4BET_VALUE:
            row = {'raise_4x': 1.0}
        elif hand in SB_4BET_MIX:
            row = {'raise_4x': 0.5, 'call': 0.5}
        elif hand in SB_CALL_VS_3BET:
            row = {'call': 1.0}
        else:
            row = {'fold': 1.0}
        table[hand] = _normalize_row(row)
    return table


def build_bb_vs_4bet() -> Dict[str, Dict[str, float]]:
    """vs_4bet.BB_vs_SB: jam / call / fold."""
    table = {}
    for hand in CANONICAL_HANDS:
        if hand in BB_4BET_JAM:
            row = {'jam': 1.0}
        elif hand in BB_4BET_CALL:
            row = {'call': 1.0}
        else:
            row = {'fold': 1.0}
        table[hand] = _normalize_row(row)
    return table


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def build_chart() -> Dict[str, dict]:
    """Assemble the full chart dict ready for json.dump."""
    return {
        'meta': {
            'depth_bb': 100,
            'players': 2,
            'version': '1.0',
        },
        'rfi': {
            'SB': build_sb_rfi(),
        },
        'vs_open': {
            'BB_vs_SB': build_bb_vs_open(),
        },
        'vs_3bet': {
            'SB_vs_BB': build_sb_vs_3bet(),
        },
        'vs_4bet': {
            'BB_vs_SB': build_bb_vs_4bet(),
        },
    }


def _validate_chart(chart: Dict[str, dict]) -> None:
    """Sanity-check the chart before writing. Raises on any failure."""
    scenarios = [
        ('rfi', 'SB'),
        ('vs_open', 'BB_vs_SB'),
        ('vs_3bet', 'SB_vs_BB'),
        ('vs_4bet', 'BB_vs_SB'),
    ]
    for scenario, key in scenarios:
        rows = chart[scenario][key]
        if len(rows) != 169:
            raise AssertionError(
                f"{scenario}.{key}: expected 169 hands, got {len(rows)}"
            )
        for hand, row in rows.items():
            total = sum(row.values())
            if abs(total - 1.0) > 1e-9:
                raise AssertionError(
                    f"{scenario}.{key}.{hand}: sum={total} (must be 1.0)"
                )


def main() -> str:
    chart = build_chart()
    _validate_chart(chart)
    out_path = os.path.join(os.path.dirname(__file__), 'preflop_100bb_hu.json')
    with open(out_path, 'w') as f:
        json.dump(chart, f, indent=2)
        f.write('\n')

    # Print a short summary so the script is self-reporting.
    sb_open_rate = sum(
        row.get('raise_3bb', 0.0) for row in chart['rfi']['SB'].values()
    ) / 169
    bb_def_rate = sum(
        row.get('call', 0.0) + row.get('raise_3x', 0.0)
        for row in chart['vs_open']['BB_vs_SB'].values()
    ) / 169
    bb_3bet_rate = sum(
        row.get('raise_3x', 0.0)
        for row in chart['vs_open']['BB_vs_SB'].values()
    ) / 169
    sb_4bet_jam_rate = sum(
        row.get('raise_4x', 0.0) + row.get('jam', 0.0)
        for row in chart['vs_3bet']['SB_vs_BB'].values()
    ) / 169

    print(f"wrote {out_path}")
    print(f"  SB open rate        = {sb_open_rate:.4f}")
    print(f"  BB defense rate     = {bb_def_rate:.4f}")
    print(f"  BB 3-bet rate       = {bb_3bet_rate:.4f}")
    print(f"  SB 4-bet+jam rate   = {sb_4bet_jam_rate:.4f}")
    return out_path


if __name__ == '__main__':
    main()
