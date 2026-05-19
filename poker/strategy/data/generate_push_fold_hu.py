"""Generate `push_fold_hu.json` from the per-hand rules below.

Source-of-truth for the rules: `push_fold_hu_README.md` (sibling file).
This script encodes the v1 approximations in a deterministic shape:
- 5 bucketed stack depths (5, 7, 10, 12, 15 BB)
- Two scenarios per depth: SB push-fold, BB call-vs-jam
- Binary 100/0 frequencies per canonical hand

Re-run after edits:
    docker compose exec backend python -m poker.strategy.data.generate_push_fold_hu

Reviewers should compare the output against canonical Nash push/fold
charts (HRC, ICMIZER, WizardOfOdds) and file border-flip entries in
the README for any disagreements found.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Set


RANKS = "23456789TJQKA"
RANK_INDEX = {r: i for i, r in enumerate(RANKS)}
# Top rank value used to compare two cards within a hand
RANK_VALUE = {r: i + 2 for i, r in enumerate(RANKS)}


def all_canonical_hands() -> List[str]:
    """169 canonical hands in stable order: pairs, then suited high-low, then offsuit high-low."""
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
assert len(CANONICAL_HANDS) == 169


def _parse_hand(hand: str):
    """Returns (high_rank, low_rank, suited_bool, is_pair_bool).
    For pairs, suited is False and is_pair is True."""
    if len(hand) == 2:
        # pair like 'AA'
        return hand[0], hand[0], False, True
    return hand[0], hand[1], hand[2] == 's', False


# ── Range rules ──────────────────────────────────────────────────────────
#
# v1 approximate Nash ranges. Each function returns True if the hand
# belongs in the SB push range or BB call range at the given depth.
# Wider at shallower depths; tighter at deeper ones. See README for
# aggregate frequency targets.


def _hand_strength_rank(hand: str) -> int:
    """Crude proxy for hand strength — used to define rangeprogressions.

    Higher number = stronger hand. Ordering is approximate equity-vs-random
    rank. Used by the range threshold tables below to pick the top-K hands.
    Doesn't need to be perfect; the threshold tables snap to coarse bands.

    Pair offset (+1000) ensures pocket pairs always rank above any
    non-pair — required so 22 sits above K2o-style trash, AA above AKs,
    etc. Magnitudes inside each tier preserve the within-tier ordering
    (AA > KK; AKs > AKo > Q5s > 32s) without inversions.
    """
    high, low, suited, is_pair = _parse_hand(hand)
    if is_pair:
        # Pairs always strongest; intra-pair ordering by rank.
        return 1000 + RANK_VALUE[high]
    high_v = RANK_VALUE[high]
    low_v = RANK_VALUE[low]
    # Heavy weighting on the high card so AKx > Q9x > 64x regardless of
    # connectedness. Low card has 1/5 weight so suited connectors
    # don't outrank suited Broadway.
    score = high_v * 10 + low_v
    if suited:
        score += 12
    gap = high_v - low_v
    if gap == 1:
        score += 4  # connectors
    elif gap == 2:
        score += 2  # one-gappers
    return score


def _hands_sorted_by_strength() -> List[str]:
    return sorted(CANONICAL_HANDS, key=_hand_strength_rank, reverse=True)


_SORTED = _hands_sorted_by_strength()


def _top_n_set(n: int) -> Set[str]:
    """Return the top-N hands by the proxy strength rank."""
    return set(_SORTED[:n])


# Approximate Nash push/fold range sizes per depth. Tuned to hit the
# aggregate frequency bands in the README; refine later when canonical
# Nash data is on hand.
SB_PUSH_TOP_N_BY_DEPTH: Dict[int, int] = {
    5: 135,   # ~80% — almost any two
    7: 95,    # ~56%
    10: 76,   # ~45%
    12: 60,   # ~35%
    15: 42,   # ~25%
}

BB_CALL_TOP_N_BY_DEPTH: Dict[int, int] = {
    5: 76,    # ~45%
    7: 51,    # ~30%
    10: 36,   # ~21%
    12: 27,   # ~16%
    15: 22,   # ~13%
}


def build_sb_push_fold(depth_bb: int) -> Dict[str, Dict[str, float]]:
    """For one stack depth, return {hand: {action: probability}} for SB."""
    push_set = _top_n_set(SB_PUSH_TOP_N_BY_DEPTH[depth_bb])
    out = {}
    for hand in CANONICAL_HANDS:
        if hand in push_set:
            out[hand] = {"jam": 1.0}
        else:
            out[hand] = {"fold": 1.0}
    return out


def build_bb_call_jam(depth_bb: int) -> Dict[str, Dict[str, float]]:
    """For one stack depth, return {hand: {action: probability}} for BB facing SB jam."""
    call_set = _top_n_set(BB_CALL_TOP_N_BY_DEPTH[depth_bb])
    out = {}
    for hand in CANONICAL_HANDS:
        if hand in call_set:
            out[hand] = {"call": 1.0}
        else:
            out[hand] = {"fold": 1.0}
    return out


def build_chart() -> Dict:
    chart = {
        "meta": {
            "format": "hu_push_fold_v1",
            "version": "1.0",
            "depth_bb_buckets": sorted(SB_PUSH_TOP_N_BY_DEPTH.keys()),
            "calibration_status": "v1_placeholder_needs_nash_verification",
        }
    }
    for depth in sorted(SB_PUSH_TOP_N_BY_DEPTH.keys()):
        chart[f"{depth}bb"] = {
            "sb_open": build_sb_push_fold(depth),
            "bb_vs_jam": build_bb_call_jam(depth),
        }
    return chart


def main() -> int:
    out_path = os.path.join(os.path.dirname(__file__), "push_fold_hu.json")
    data = build_chart()
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Wrote {out_path}")
    print()
    print("Aggregate push/call rates (sanity check vs README bands):")
    for depth in sorted(SB_PUSH_TOP_N_BY_DEPTH.keys()):
        sb_count = SB_PUSH_TOP_N_BY_DEPTH[depth]
        bb_count = BB_CALL_TOP_N_BY_DEPTH[depth]
        print(
            f"  {depth:>2} BB: SB push {sb_count/169*100:>5.1f}% "
            f"({sb_count} hands); BB call {bb_count/169*100:>5.1f}% ({bb_count} hands)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
