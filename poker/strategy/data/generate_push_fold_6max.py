"""Generate `push_fold_6max.json` from the readable range strings below.

Source-of-truth for the *conventions*: `push_fold_6max_README.md` (sibling).
Source-of-truth for the *ranges*: `docs/plans/PUSH_FOLD_6MAX_SCOPE.md`
(the published-Nash chip-EV ICM-off research spec).

This script mirrors `generate_push_fold_hu.py` / `generate_hu_chart.py`:
- Walks the 169 canonical hands in stable order.
- Expands readable range strings ("22+, A2s+, A7o+, KTs+, ...") into
  canonical-hand sets via a notation parser (extended here to also accept
  the "any ace" shorthand `Ax` / `Axs` / `Axo` and "any two").
- Emits binary 100/0 jam-or-fold (or call-or-fold) frequencies per hand.

Schema (see scope doc):
    {
      "meta": {...},
      "unopened":     {position: {depth: {hand: {action: prob}}}},
      "call_vs_shove": {"bb_vs_sb":   {depth: {hand: {...}}},
                        "bb_vs_late": {depth: {hand: {...}}}}
    }

Re-run after edits:
    docker compose exec backend python -m poker.strategy.data.generate_push_fold_6max
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Set

RANKS = "23456789TJQKA"
RANK_INDEX = {r: i for i, r in enumerate(RANKS)}


def all_canonical_hands() -> List[str]:
    """169 canonical hands in stable order: pairs, then suited high-low,
    then offsuit high-low. Identical ordering to the HU generator."""
    hands: List[str] = []
    for r in RANKS:
        hands.append(f"{r}{r}")
    for i in range(len(RANKS)):
        for j in range(i + 1, len(RANKS)):
            high, low = RANKS[j], RANKS[i]
            hands.append(f"{high}{low}s")
            hands.append(f"{high}{low}o")
    return hands


CANONICAL_HANDS = all_canonical_hands()
assert len(CANONICAL_HANDS) == 169, len(CANONICAL_HANDS)


# Number of card combinations per canonical hand class. Published Nash
# percentages are *combo-weighted* (an offsuit hand is 12 of 1326 combos,
# a suited hand 4, a pair 6), so aggregate frequencies must be measured in
# combos — not as a fraction of the 169 hand classes (which would
# over-weight the 78 offsuit classes). Total = 1326.
def hand_combos(hand: str) -> int:
    if len(hand) == 2:
        return 6  # pocket pair
    return 4 if hand.endswith("s") else 12


TOTAL_COMBOS = sum(hand_combos(h) for h in CANONICAL_HANDS)
assert TOTAL_COMBOS == 1326, TOTAL_COMBOS


# Strength ordering (strongest-first) used to trim an over-listed range down
# to its published target frequency. Reuses the HU generator's
# `_hand_strength_rank` proxy (pairs > aces > kings > ... ; suited > offsuit;
# connectors/one-gappers nudged up) so the trim drops the weakest combos in
# a range, preserving its shape. Imported lazily to avoid a hard module
# dependency when only the parser is needed.
def _strength_order() -> Dict[str, int]:
    from poker.strategy.data.generate_push_fold_hu import (
        _hands_sorted_by_strength,
    )

    return {h: i for i, h in enumerate(_hands_sorted_by_strength())}


_STRENGTH_RANK = _strength_order()  # 0 == strongest


# ── Range-notation parser ─────────────────────────────────────────────────
#
# Mirrors generate_hu_chart.py's parser, extended to handle the extra
# shorthand the 6-max scope doc uses:
#   "any two"     -> all 169 hands
#   "Ax"          -> any ace (suited + offsuit), i.e. A2s+ and A2o+
#   "Axs"         -> any suited ace (A2s+)
#   "Axo"         -> any offsuit ace (A2o+)
#   (likewise Kx/Qx/Jx/Tx ...)


def _all_x_with_high(high: str, suit: str) -> Set[str]:
    """All non-pair hands with the given high rank and suit ('s'/'o').

    e.g. high='Q', suit='s' -> Q2s..QJs.  high='A', suit='o' -> A2o..AKo.
    """
    hi_idx = RANK_INDEX[high]
    return {f"{high}{RANKS[k]}{suit}" for k in range(hi_idx)}


def _parse_x_token(token: str) -> Set[str]:
    """Parse an 'any kicker' token like 'Ax', 'Kxs', 'Qxo'."""
    high = token[0]
    if high not in RANK_INDEX:
        raise ValueError(f"Bad x-token: {token!r}")
    suit_part = token[2:]  # after 'x'
    if suit_part == "":
        return _all_x_with_high(high, "s") | _all_x_with_high(high, "o")
    if suit_part == "s":
        return _all_x_with_high(high, "s")
    if suit_part == "o":
        return _all_x_with_high(high, "o")
    raise ValueError(f"Bad x-token suit: {token!r}")


def _parse_pair_token(token: str) -> Set[str]:
    """Parse a pocket-pair token: '22', 'TT', '22+', '77+'."""
    base = token.rstrip("+")
    if len(base) != 2 or base[0] != base[1] or base[0] not in RANK_INDEX:
        raise ValueError(f"Bad pair token: {token!r}")
    if token.endswith("+"):
        idx = RANK_INDEX[base[0]]
        return {f"{r}{r}" for r in RANKS[idx:]}
    return {base}


def _parse_single_hand(token: str) -> Set[str]:
    if len(token) != 3:
        raise ValueError(f"Bad single hand token: {token!r}")
    hi, lo, suit = token[0], token[1], token[2]
    if hi not in RANK_INDEX or lo not in RANK_INDEX or suit not in ("s", "o"):
        raise ValueError(f"Bad single hand token: {token!r}")
    if RANK_INDEX[hi] <= RANK_INDEX[lo]:
        raise ValueError(f"Hand must be high-rank-first: {token!r}")
    return {token}


def _parse_plus_token(base: str) -> Set[str]:
    """e.g. 'A2s' (with '+' stripped) -> A2s, A3s, ..., AKs."""
    if len(base) != 3:
        raise ValueError(f"Bad +-token base: {base!r}")
    hi, lo, suit = base[0], base[1], base[2]
    if hi not in RANK_INDEX or lo not in RANK_INDEX or suit not in ("s", "o"):
        raise ValueError(f"Bad +-token base: {base!r}")
    hi_idx = RANK_INDEX[hi]
    lo_idx = RANK_INDEX[lo]
    if lo_idx >= hi_idx:
        raise ValueError(f"Bad +-token base: {base!r}")
    return {f"{hi}{RANKS[k]}{suit}" for k in range(lo_idx, hi_idx)}


def _parse_explicit_range(a: str, b: str) -> Set[str]:
    """Both endpoints share the same high rank and suit; kickers form a range."""
    if len(a) != 3 or len(b) != 3:
        raise ValueError(f"Bad explicit range: {a}-{b}")
    if a[0] != b[0] or a[2] != b[2]:
        raise ValueError(f"Range endpoints must share high rank + suit: {a}-{b}")
    hi, suit = a[0], a[2]
    lo_a, lo_b = RANK_INDEX[a[1]], RANK_INDEX[b[1]]
    lo_min, lo_max = sorted([lo_a, lo_b])
    return {f"{hi}{RANKS[k]}{suit}" for k in range(lo_min, lo_max + 1)}


def _parse_unpaired_token(token: str) -> Set[str]:
    if "-" in token:
        a, b = token.split("-", 1)
        return _parse_explicit_range(a, b)
    # 'x' shorthand: second char is literally 'x' (any kicker).
    if len(token) >= 2 and token[1] == "x":
        return _parse_x_token(token)
    if token.endswith("+"):
        return _parse_plus_token(token[:-1])
    return _parse_single_hand(token)


def expand_range(spec: str) -> Set[str]:
    """Expand a comma-separated range string into a canonical-hand set."""
    spec_norm = spec.strip().lower()
    if spec_norm in ("any two", "any2", "anytwo", "100%"):
        return set(CANONICAL_HANDS)
    result: Set[str] = set()
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if token.lower() in ("any two", "any2"):
            result |= set(CANONICAL_HANDS)
            continue
        # Pair token: first two chars equal and no suit letter.
        if len(token.rstrip("+")) == 2 and token[0] == token[1] and token[0] in RANK_INDEX:
            result |= _parse_pair_token(token)
        else:
            result |= _parse_unpaired_token(token)
    return result


def expand_many(*specs: str) -> Set[str]:
    result: Set[str] = set()
    for s in specs:
        result |= expand_range(s)
    return result


# ── Unopened shove ranges (per position × depth) ──────────────────────────
#
# Transcribed verbatim from docs/plans/PUSH_FOLD_6MAX_SCOPE.md "Unopened
# shove" tables. Depth keys are ints (BB). Each value is a readable range
# string fed to expand_range(). "any two" => ~100%.

UNOPENED_RANGES: Dict[str, Dict[int, str]] = {
    "UTG": {
        4: "22+, A2s+, A2o+, K8s+, KTo+, QTs+, JTs",
        6: "22+, A2s+, A7o+, K9s+, KJo+, QTs+, JTs",
        8: "22+, A2s+, A7o+, KTs+, KJo+, QJs",
        10: "55+, ATs+, AJo+, KQs",
        12: "66+, ATs+, AJo+, KQs",
        15: "77+, AJs+, AQo+, KQs",
    },
    "HJ": {
        4: "22+, A2s+, A2o+, K6s+, K9o+, Q9s+, QTo+, J9s+, T9s",
        6: "22+, A2s+, A4o+, K9s+, KTo+, Q9s+, QJo, JTs",
        8: "22+, A2s+, A7o+, KTs+, KJo+, QJs",
        10: "33+, A8s+, ATo+, KTs+, KQo, QJs",
        12: "22+, A8s+, A8o+, KTs+, KJo+, QTs+, JTs",
        15: "44+, ATs+, AJo+, KTs+, KQo, QJs",
    },
    "CO": {
        4: "22+, Ax, Kx, Qxs, Q7o+, Jxs, J8o+, T7s+, T9o, 97s+",
        6: "22+, Ax, K7s+, K9o+, Q9s+, QTo+, J9s+, T9s, 98s",
        8: "22+, Ax, Kx, Qxs, Q5o+, Jxs, J8o+, T7s+, T9o, 97s+",
        10: "22+, Axs, A5o+, Kxs, K9o+, Q9s+, QTo+, JTs",
        12: "22+, Axs, A8o+, KTs+, KJo+, QTs+, JTs",
        15: "44+, ATs+, AJo+, KTs+, KQo, QJs",
    },
    "BTN": {
        4: "any two",
        6: "22+, Ax, Kx, Qx, Jxs, J5o+, Txs, T7o+, 9xs, 97o+, 8xs, 86s+, 75s+, 65s",
        8: "22+, Ax, Kx, Qx, Jxs, J5o+, Txs, T7o+, 9xs, 97o+, 8xs",
        10: "22+, Ax, Kxs, K5o+, Qxs, Q7o+, Jxs, J8o+, T7s+, T9o",
        12: "22+, Axs, A4o+, Kxs, K8o+, Q9s+, QTo+, J9s+, JTo",
        15: "22+, Axs, A7o+, KTs+, KJo+, Q9s+, QTo+, JTs",
    },
    # SB = the HU SB Nash pusher chart (highest confidence). For the boundary
    # bands (6 BB ~60%) the scope doc gives a description rather than a clean
    # list; we use the explicit hand lists for 8/10/12/15 and a wide
    # broadway-anchored list for 6 BB that lands in the ~60% target band.
    "SB": {
        4: "any two",
        6: "22+, Ax, Kx, Qx, J6s+, J9o+, T7s+, T9o, 96s+, 86s+, 75s+, 64s+, 54s, 98o, 87o",
        8: "22+, Ax, Kx, Qx, Jx, T2s+, T6o+, 95s+, 97o+, 85s+, 75s+, 64s+, 54s",
        10: "22+, Ax, Kx, Qxs, Q3o+, Jxs, J7o+, T6s+, T8o+, 97s+, 98o, 86s+, 76s, 65s, 54s",
        12: "22+, Ax, Kxs, K4o+, Qxs, Q7o+, J7s+, J9o+, T7s+, T9o, 97s+, 87s, 76s, 65s",
        15: "22+, Axs, A2o+, Kxs, K7o+, Q8s+, Q9o+, J8s+, JTo, T8s+, 98s, 87s",
    },
}


# ── Call-vs-shove ranges ───────────────────────────────────────────────────

CALL_VS_SHOVE_RANGES: Dict[str, Dict[int, str]] = {
    # BB vs SB jam — canonical HU Nash caller chart [H].
    "bb_vs_sb": {
        4: "22+, A2s+, A2o+, K2s+, K2o+, Q2s+, Q4o+, J4s+, J7o+, T6s+, T8o+, 96s+, 98o, 86s+, 75s+, 65s, 54s",
        6: "22+, A2s+, A2o+, K2s+, K5o+, Q5s+, Q9o+, J7s+, JTo, T7s+, T9o, 97s+, 87s, 76s",
        8: "22+, A2s+, A4o+, K5s+, K9o+, Q8s+, QTo+, J8s+, JTo, T8s+, 98s",
        10: "22+, A2s+, A7o+, K9s+, KTo+, Q9s+, QTo+, J9s+, T9s",
        12: "22+, A3s+, A9o+, KTs+, KJo+, QTs+, QJo, JTs",
        15: "33+, ATs+, AJo+, KJs+, KQo, QJs",
    },
    # Blinds vs a late-position (BTN/CO) jam [M]. No 4 BB row in the source
    # (blind is committed vs any late jam at 4 BB) — the lookup clamps a
    # 4 BB late-jam call up to the 6 BB row.
    "bb_vs_late": {
        6: "22+, A2s+, A4o+, K7s+, K9o+, Q9s+, QTo+, J9s+, T9s",
        8: "22+, A2s+, A7o+, K9s+, KTo+, Q9s+, QTo+, J9s+, T9s",
        10: "22+, A2s+, A9o+, KTs+, KJo+, QTs+, QJo, JTs",
        12: "22+, A5s+, ATo+, KJs+, KQo, QJs",
        15: "44+, ATs+, AJo+, KQs",
    },
}


# ── Reshove ranges (jam over a single non-all-in open) ─────────────────────
#
# [L] confidence — extrapolated, NOT cross-validated (PUSH_FOLD_6MAX_SCOPE.md).
# Keyed on HERO's effective stack only (8/10/12/15 BB); opener-position-agnostic
# for v1 (reshoving vs a tight UTG open should be tighter — a future refinement).
# No 4/6 BB rows: at <=6 BB facing an open the blind is committed and the
# decision degenerates; the lookup clamps a sub-8 BB reshove up to the 8 BB row.
RESHOVE_RANGES: Dict[int, str] = {
    8: "22+, A4s+, A8o+, K9s+, KJo+, Q9s+, QJo, JTs",
    10: "33+, A7s+, A9o+, KTs+, KJo+, QTs+, QJo",
    12: "44+, A9s+, ATo+, KJs+, KQo, QJs",
    15: "55+, ATs+, AJo+, KQs, AKo",
}

RESHOVE_TARGET_PCT: Dict[int, float] = {8: 16, 10: 13, 12: 10, 15: 7}

RESHOVE_DEPTH_BUCKETS = [8, 10, 12, 15]


# Published target jam frequency (combo-weighted %) per position × depth,
# taken from the scope doc's "~%" column. These are the cross-validated [H]
# anchors; the readable hand lists above define the *shape* (which hands and
# in what priority), but several lists transcribe looser than their stated %.
# The build trims each expanded list down to this target by dropping the
# weakest combos (by `_STRENGTH_RANK`) so the chart honors BOTH the published
# percentages and the doc's range shape. See README "v1 calibration status".
UNOPENED_TARGET_PCT: Dict[str, Dict[int, float]] = {
    "UTG": {4: 18, 6: 12, 8: 9, 10: 6.2, 12: 6, 15: 5},
    "HJ": {4: 24, 6: 14, 8: 9, 10: 9.5, 12: 11, 15: 8},
    "CO": {4: 38, 6: 22, 8: 30, 10: 15.8, 12: 12, 15: 10},
    "BTN": {4: 100, 6: 52, 8: 40, 10: 26.8, 12: 20, 15: 16},
    "SB": {4: 100, 6: 60, 8: 52, 10: 37.5, 12: 30, 15: 22},
}

CALL_TARGET_PCT: Dict[str, Dict[int, float]] = {
    "bb_vs_sb": {4: 55, 6: 42, 8: 33, 10: 24.5, 12: 19, 15: 13},
    "bb_vs_late": {6: 28, 8: 24, 10: 18, 12: 14, 15: 9},
}


# Confidence tags per (table, depth) — from the scope doc's per-cell tags.
CONFIDENCE: Dict[str, Dict[int, str]] = {
    "UTG": {4: "L", 6: "M", 8: "H", 10: "H", 12: "H", 15: "H"},
    "HJ": {4: "L", 6: "M", 8: "H", 10: "H", 12: "M", 15: "H"},
    "CO": {4: "L", 6: "M", 8: "H", 10: "H", 12: "M", 15: "M"},
    "BTN": {4: "H", 6: "M", 8: "H", 10: "H", 12: "M", 15: "H"},
    "SB": {4: "H", 6: "H", 8: "H", 10: "H", 12: "H", 15: "H"},
    "bb_vs_sb": {4: "H", 6: "H", 8: "H", 10: "H", 12: "H", 15: "H"},
    "bb_vs_late": {6: "M", 8: "M", 10: "M", 12: "M", 15: "M"},
    "reshove": {8: "L", 10: "L", 12: "L", 15: "L"},
}


DEPTH_BUCKETS = [4, 6, 8, 10, 12, 15]


def combo_pct(hand_set: Set[str]) -> float:
    """Combo-weighted percentage of the 1326-combo space covered by a set."""
    return sum(hand_combos(h) for h in hand_set) / TOTAL_COMBOS * 100


def trim_to_target(hand_set: Set[str], target_pct: float) -> Set[str]:
    """Trim an expanded range down to `target_pct` (combo-weighted), keeping
    the strongest combos by `_STRENGTH_RANK`.

    Drops the weakest-listed combos when the readable hand list overshoots
    its published frequency. If the list is already at or under the target,
    it's returned unchanged (the doc's list is the upper bound on what's
    eligible — we never *add* hands the doc didn't list). Always keeps at
    least the single strongest hand.
    """
    cap = target_pct / 100 * TOTAL_COMBOS
    if combo_pct(hand_set) <= target_pct:
        return set(hand_set)
    ordered = sorted(hand_set, key=lambda h: _STRENGTH_RANK.get(h, 9999))
    kept: Set[str] = set()
    acc = 0
    for hand in ordered:
        c = hand_combos(hand)
        # Round to nearest: include while the midpoint of this hand's combos
        # stays under the cap (or nothing kept yet).
        if not kept or acc + c / 2 <= cap:
            kept.add(hand)
            acc += c
        else:
            break
    return kept


def _build_action_row(hand_set: Set[str], in_action: str) -> Dict[str, Dict[str, float]]:
    """Return {hand: {action: 1.0}} for all 169 hands; in-range hands get
    `in_action`, the rest 'fold'."""
    out: Dict[str, Dict[str, float]] = {}
    for hand in CANONICAL_HANDS:
        if hand in hand_set:
            out[hand] = {in_action: 1.0}
        else:
            out[hand] = {"fold": 1.0}
    return out


def build_chart() -> Dict:
    chart: Dict = {
        "meta": {
            "format": "push_fold_6max_v1",
            "version": "1.0",
            "model": "chip_ev_nash_icm_off",
            "ante": False,
            "depth_bb_buckets": list(DEPTH_BUCKETS),
            "calibration_status": "v1_from_published_nash",
            "confidence": CONFIDENCE,
        },
        "unopened": {},
        "call_vs_shove": {},
        "reshove": {},
    }

    for position, by_depth in UNOPENED_RANGES.items():
        chart["unopened"][position] = {}
        for depth in DEPTH_BUCKETS:
            hand_set = expand_range(by_depth[depth])
            hand_set = trim_to_target(hand_set, UNOPENED_TARGET_PCT[position][depth])
            chart["unopened"][position][str(depth)] = _build_action_row(hand_set, "jam")

    for table, by_depth in CALL_VS_SHOVE_RANGES.items():
        chart["call_vs_shove"][table] = {}
        for depth in sorted(by_depth.keys()):
            hand_set = expand_range(by_depth[depth])
            hand_set = trim_to_target(hand_set, CALL_TARGET_PCT[table][depth])
            chart["call_vs_shove"][table][str(depth)] = _build_action_row(hand_set, "call")

    # Reshove: jam-or-fold over a single non-all-in open, depth-keyed only.
    for depth in RESHOVE_DEPTH_BUCKETS:
        hand_set = expand_range(RESHOVE_RANGES[depth])
        hand_set = trim_to_target(hand_set, RESHOVE_TARGET_PCT[depth])
        chart["reshove"][str(depth)] = _build_action_row(hand_set, "jam")

    return chart


def main() -> int:
    out_path = os.path.join(os.path.dirname(__file__), "push_fold_6max.json")
    data = build_chart()
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Wrote {out_path}")
    print()
    print("Combo-weighted jam% by position × depth (trimmed → vs target):")
    header = "  pos  " + "".join(f"{d:>9}BB" for d in DEPTH_BUCKETS)
    print(header)
    for position in UNOPENED_RANGES:
        cells = []
        for depth in DEPTH_BUCKETS:
            hs = trim_to_target(
                expand_range(UNOPENED_RANGES[position][depth]),
                UNOPENED_TARGET_PCT[position][depth],
            )
            cells.append(f"{combo_pct(hs):>5.1f}/{UNOPENED_TARGET_PCT[position][depth]:<4.0f}")
        print(f"  {position:<5}" + "".join(f"{c:>11}" for c in cells))
    print()
    print("Combo-weighted call% by table × depth (trimmed → vs target):")
    for table, by_depth in CALL_VS_SHOVE_RANGES.items():
        cells = []
        for depth in sorted(by_depth.keys()):
            hs = trim_to_target(expand_range(by_depth[depth]), CALL_TARGET_PCT[table][depth])
            cells.append(f"{depth}BB={combo_pct(hs):.1f}/{CALL_TARGET_PCT[table][depth]:.0f}")
        print(f"  {table:<12} " + "  ".join(cells))
    print()
    print("Combo-weighted reshove% by depth (trimmed → vs target) [L]:")
    cells = []
    for depth in RESHOVE_DEPTH_BUCKETS:
        hs = trim_to_target(expand_range(RESHOVE_RANGES[depth]), RESHOVE_TARGET_PCT[depth])
        cells.append(f"{depth}BB={combo_pct(hs):.1f}/{RESHOVE_TARGET_PCT[depth]:.0f}")
    print("  reshove      " + "  ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
